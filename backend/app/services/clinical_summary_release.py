"""Deterministic summary projection inside the existing two-transaction release.

The unchanged 4F gate verifies source FACT_RESTATEMENTs, not arithmetic/missingness
claims. The summary policy separately recomputes those fields from the locked
accepted snapshot in T1 and T2. The intent binds the ENTIRE representation bytes.
No fabricated absence claims, clinical gate outcomes or extra audit prose.
"""

import json
from typing import Any, cast
from uuid import UUID, uuid5

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ResponseReleaseIntent
from app.schemas.clinical_summary import (
    ClinicalSummaryReleaseRequest,
    ClinicalSummaryResponse,
)
from app.schemas.draft_verification import (
    DraftClaim,
    DraftVerificationRequest,
    RestatementContent,
    SourceAttribution,
)
from app.schemas.evidence_aware_draft import EvidenceAwareDraftRequest
from app.schemas.response_release import RELEASE_POLICY, SUMMARY_REPRESENTATION
from app.schemas.verification_gate import GateDecisionRecord
from app.services.authorization import authorize_patient_scope
from app.services.clinical_summary import POLICY_VERSION, summarize_patient_records
from app.services.evidence_aware_evaluator import catalogue_fingerprint
from app.services.response_release import (
    _Prepared,
    _receipt,
    body_digest,
    get_release_intent,
)
from app.services.verification_audit import (
    get_patient_audit_event,
    project_gate,
    record_patient_gate_evaluation,
)
from app.services.verification_gate import evaluate_patient_gate


async def _evaluate(
    db: AsyncSession,
    actor_id: UUID,
    patient_id: UUID,
    request: ClinicalSummaryReleaseRequest,
    operation_id: UUID,
) -> tuple[EvidenceAwareDraftRequest, GateDecisionRecord, bytes]:
    request = ClinicalSummaryReleaseRequest.model_validate(request.model_dump(warnings=False))
    if request.patient_id != patient_id:
        raise HTTPException(403, "Summary scope unavailable")
    summary = (await summarize_patient_records(db, actor_id, patient_id)).summary
    if summary.patient_id != patient_id or summary.policy_version != POLICY_VERSION:
        raise HTTPException(409, "Summary context unavailable")
    sources = {
        record.source.version_id: record.source
        for point in (
            *summary.hba1c_observations,
            *((summary.latest_egfr,) if summary.latest_egfr else ()),
        )
        for record in point.records
    }
    sources.update({r.source.version_id: r.source for r in summary.medications})
    # Preserve the existing gate/audit's nonempty, 32-claim ceiling. Never truncate
    # or turn an absence assertion into a fictitious accepted clinical fact.
    if not 1 <= len(sources) <= 32:
        raise HTTPException(409, "Summary exceeds supported release context")
    draft_id = uuid5(operation_id, SUMMARY_REPRESENTATION)
    draft = EvidenceAwareDraftRequest(
        draft=DraftVerificationRequest(
            draft_id=draft_id,
            patient_id=patient_id,
            claims=tuple(
                DraftClaim(
                    claim_id=uuid5(draft_id, str(row.version_id)),
                    patient_id=patient_id,
                    claim_type="FACT_RESTATEMENT",
                    fact_version_ids=(row.version_id,),
                    source_attribution=SourceAttribution(
                        author_id=row.author_id,
                        author_role=row.author_role,
                        category=row.attribution,
                    ),
                    proposed_content=RestatementContent(
                        fact=cast(Any, row.fact.model_dump()),
                        observed_at=row.observed_at,
                    ),
                )
                for row in sorted(sources.values(), key=lambda r: str(r.version_id))
            ),
        ),
        bindings=(),
    )
    gate = (await evaluate_patient_gate(db, actor_id, patient_id, draft)).gate
    if (
        gate.decision != "PASS"
        or gate.verification is None
        or gate.verification.snapshot != summary.snapshot
        or gate.verification.grounding_policy_version != summary.verification_policy_version
        or gate.verification.renderer_version != summary.renderer_version
    ):
        raise HTTPException(409, "Summary verification unavailable")
    body = json.dumps(
        ClinicalSummaryResponse(summary=summary).model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return draft, gate, body


async def prepare_summary_release(
    db: AsyncSession,
    actor_id: UUID,
    patient_id: UUID,
    request: ClinicalSummaryReleaseRequest,
    operation_id: UUID,
) -> _Prepared:
    """T1: fresh server evaluation; caller commits audit+intent before any send."""
    draft, gate, body = await _evaluate(db, actor_id, patient_id, request, operation_id)
    event = await record_patient_gate_evaluation(db, actor_id, patient_id, draft, operation_id)
    if project_gate(gate, actor_id, event.metadata.actor_role) != event.metadata:
        raise HTTPException(409, "Summary context changed")
    if (
        await db.scalar(
            select(ResponseReleaseIntent.id).where(
                ResponseReleaseIntent.evaluation_event_id == event.id,
            )
        )
        is not None
    ):
        raise HTTPException(409, "Release attempt already prepared; automatic replay denied")
    row = ResponseReleaseIntent(
        evaluation_event_id=event.id,
        release_policy_version=RELEASE_POLICY,
        representation_version=SUMMARY_REPRESENTATION,
        body_digest=body_digest(body),
    )
    db.add(row)
    await db.flush()
    return _Prepared(_receipt(row))


async def revalidate_summary_release(
    db: AsyncSession,
    actor_id: UUID,
    patient_id: UUID,
    request: ClinicalSummaryReleaseRequest,
    operation_id: UUID,
    prepared: _Prepared,
) -> tuple[bytes, str]:
    """T2: reauthorize/rebuild independently; no supplied summary or cached bytes."""
    if prepared.intent is None:
        raise HTTPException(409, "Summary release unavailable")
    _, gate, body = await _evaluate(db, actor_id, patient_id, request, operation_id)
    intent = await get_release_intent(db, actor_id, patient_id, prepared.intent.id)
    event = await get_patient_audit_event(db, actor_id, patient_id, intent.evaluation_event_id)
    if (
        intent != prepared.intent
        or intent.representation_version != SUMMARY_REPRESENTATION
        or event.operation_id != operation_id
        or project_gate(gate, actor_id, event.metadata.actor_role) != event.metadata
        or body_digest(body) != intent.body_digest
    ):
        raise HTTPException(409, "Summary release context changed")
    await authorize_patient_scope(db, actor_id, patient_id)
    fingerprint = gate.verification.catalogue_fingerprint if gate.verification else None
    if fingerprint is None or catalogue_fingerprint() != fingerprint:
        raise HTTPException(503, "Release catalogue unavailable")
    return body, fingerprint
