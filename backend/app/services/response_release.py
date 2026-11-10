"""Private preparation/revalidation. Only the ASGI orchestrator owns handoff."""

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ResponseReleaseIntent, VerificationAuditEvent
from app.schemas.evidence_aware_draft import EvidenceAwareDraftRequest
from app.schemas.response_release import (
    EVIDENCE_REPRESENTATION,
    RELEASE_POLICY,
    REPRESENTATION,
    RecordResponse,
    ReleaseIntent,
)
from app.schemas.verification_gate import GateDecisionRecord
from app.services.authorization import authorize_patient_scope
from app.services.clinical_facts import lock_patient_scope
from app.services.evidence_aware_evaluator import catalogue_fingerprint
from app.services.evidence_provenance import evidence_response
from app.services.verification_audit import (
    get_patient_audit_event,
    project_gate,
    record_patient_gate_evaluation,
)
from app.services.verification_gate import evaluate_patient_gate


@dataclass(frozen=True)
class _Prepared:
    # Private, transaction-local until the orchestrator acknowledges commit.
    # Never supplied by a caller or accepted as an externally reusable permit.
    intent: ReleaseIntent | None


def encode_records(gate: GateDecisionRecord, *, include_provenance: bool = False) -> bytes:
    if gate.decision != "PASS" or not gate.rendered_records:
        raise HTTPException(409, "Verified response withheld")
    representation = (
        evidence_response(gate)
        if include_provenance
        else RecordResponse(records=gate.rendered_records)
    )
    return json.dumps(
        representation.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def body_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _receipt(row: ResponseReleaseIntent) -> ReleaseIntent:
    try:
        return ReleaseIntent.model_validate(
            dict(
                id=row.id,
                evaluation_event_id=row.evaluation_event_id,
                release_policy_version=row.release_policy_version,
                representation_version=row.representation_version,
                body_digest=row.body_digest,
                recorded_at=row.recorded_at,
            )
        )
    except ValidationError:
        raise HTTPException(503, "Release intent unavailable") from None


async def prepare_release(
    db: AsyncSession,
    actor_id: UUID,
    patient_id: UUID,
    request: EvidenceAwareDraftRequest,
    operation_id: UUID,
    *,
    include_provenance: bool = False,
) -> _Prepared:
    """No commit here; caller is the dedicated transaction-1 orchestrator only."""
    gate = (await evaluate_patient_gate(db, actor_id, patient_id, request)).gate
    event = await record_patient_gate_evaluation(db, actor_id, patient_id, request, operation_id)
    if project_gate(gate, actor_id, event.metadata.actor_role) != event.metadata:
        raise HTTPException(409, "Release context changed")
    if gate.decision != "PASS":
        return _Prepared(None)
    existing = await db.scalar(
        select(ResponseReleaseIntent.id).where(
            ResponseReleaseIntent.evaluation_event_id == event.id,
        )
    )
    if existing is not None:
        raise HTTPException(409, "Release attempt already prepared; automatic replay denied")
    row = ResponseReleaseIntent(
        evaluation_event_id=event.id,
        release_policy_version=RELEASE_POLICY,
        representation_version=EVIDENCE_REPRESENTATION if include_provenance else REPRESENTATION,
        body_digest=body_digest(encode_records(gate, include_provenance=include_provenance)),
    )
    db.add(row)
    await db.flush()
    return _Prepared(_receipt(row))


async def get_release_intent(
    db: AsyncSession,
    actor_id: UUID,
    patient_id: UUID,
    intent_id: UUID,
) -> ReleaseIntent:
    """Actor-only metadata lookup; never ownership or authorization to resume."""
    if db.new or db.dirty or db.deleted:
        raise HTTPException(409, "Release lookup requires a clean session")
    with db.no_autoflush:
        await lock_patient_scope(db, actor_id, patient_id)
        row = await db.scalar(
            select(ResponseReleaseIntent)
            .join(
                VerificationAuditEvent,
                ResponseReleaseIntent.evaluation_event_id == VerificationAuditEvent.id,
            )
            .where(
                ResponseReleaseIntent.id == intent_id,
                VerificationAuditEvent.patient_id == patient_id,
                VerificationAuditEvent.actor_id == actor_id,
            )
            .execution_options(populate_existing=True)
        )
    if row is None:
        raise HTTPException(404, "Release intent unavailable")
    result = _receipt(row)
    await authorize_patient_scope(db, actor_id, patient_id)
    return result


async def revalidate_release(
    db: AsyncSession,
    actor_id: UUID,
    patient_id: UUID,
    request: EvidenceAwareDraftRequest,
    prepared: _Prepared,
    *,
    include_provenance: bool = False,
) -> tuple[bytes, str]:
    """Transaction 2. Private prepared ownership comes only from this invocation."""
    if prepared.intent is None:
        raise HTTPException(409, "Verified response withheld")
    gate = (await evaluate_patient_gate(db, actor_id, patient_id, request)).gate
    intent = await get_release_intent(db, actor_id, patient_id, prepared.intent.id)
    if intent != prepared.intent:
        raise HTTPException(409, "Release context changed")
    evaluation = await get_patient_audit_event(db, actor_id, patient_id, intent.evaluation_event_id)
    if (
        gate.decision != "PASS"
        or project_gate(gate, actor_id, evaluation.metadata.actor_role) != evaluation.metadata
    ):
        raise HTTPException(409, "Release context changed")
    if intent.representation_version != (
        EVIDENCE_REPRESENTATION if include_provenance else REPRESENTATION
    ):
        raise HTTPException(409, "Release representation changed")
    body = encode_records(gate, include_provenance=include_provenance)
    if body_digest(body) != intent.body_digest:
        raise HTTPException(409, "Release representation changed")
    await authorize_patient_scope(db, actor_id, patient_id)
    fingerprint = gate.verification.catalogue_fingerprint if gate.verification else None
    if fingerprint is None or catalogue_fingerprint() != fingerprint:
        raise HTTPException(503, "Release catalogue unavailable")
    return body, fingerprint


async def transaction_identity(db: AsyncSession) -> tuple[int, int]:
    """Detect known lock/connection loss; never transparently reconnect and send."""
    if not db.is_active or not db.in_transaction():
        raise HTTPException(503, "Release transaction unavailable")
    connection = await db.connection()
    if connection.closed or connection.invalidated or not connection.in_transaction():
        raise HTTPException(503, "Release connection unavailable")
    row = (await connection.execute(text("SELECT pg_backend_pid(), txid_current()"))).one()
    return int(row[0]), int(row[1])
