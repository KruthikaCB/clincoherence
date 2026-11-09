"""Authorized metadata-only evaluation history. Caller owns commit and rollback."""

import json
from uuid import UUID

from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, VerificationAuditEvent
from app.schemas.evidence import RuleEvaluation
from app.schemas.evidence_aware_draft import GroundingAssessment
from app.schemas.verification_audit import AuditEvent, AuditMetadata
from app.schemas.verification_gate import ClaimGateDecision, GateDecisionRecord
from app.services.authorization import authorize_patient_scope, eligible_account
from app.services.clinical_facts import lock_patient_scope
from app.services.evidence_catalog import canonical_hash
from app.services.verification_gate import evaluate_patient_gate


def _checks(
    value: GroundingAssessment | RuleEvaluation | ClaimGateDecision | GateDecisionRecord,
) -> dict[str, object]:
    # Internal typed upstream records only; explicit nested whitelist, not exclude.
    return {
        "checks_run": value.checks_run,
        "checks_not_applicable": tuple(
            {"rule_id": item.rule_id, "reason_code": item.reason_code}
            for item in value.checks_not_applicable
        ),
    }


def _grounding(value: GroundingAssessment | None) -> dict[str, object] | None:
    if value is None:
        return None
    return dict(
        outcome=value.outcome,
        reason_code=value.reason_code,
        fact_version_ids=value.fact_version_ids,
        **_checks(value),
    )


def _rule(value: RuleEvaluation | None) -> dict[str, object] | None:
    if value is None:
        return None
    return dict(
        rule_id=value.rule_id,
        rule_version=value.rule_version,
        status=value.status,
        reason_code=value.reason_code,
        evidence=value.evidence,
        matched_fact_version_ids=value.matched_fact_version_ids,
        **_checks(value),
    )


def project_gate(gate: GateDecisionRecord, actor_id: UUID, actor_role: str) -> AuditMetadata:
    """Trusted gate projection only. Never accept this as a provider-supplied event.

    No rendered text, proposed values, prompts or unverified envelope prose is
    copied. New upstream fields cannot silently expand this projection.
    """
    verification = None
    ref = gate.verification
    if ref is not None:
        verification = dict(
            record_digest=ref.record_digest,
            composition_policy_version=ref.composition_policy_version,
            grounding_policy_version=ref.grounding_policy_version,
            evidence_policy_version=ref.evidence_policy_version,
            renderer_version=ref.renderer_version,
            consistency_ruleset_version=ref.consistency_ruleset_version,
            snapshot=ref.snapshot,
            consistency_findings=tuple(
                dict(
                    rule_id=f.rule_id,
                    category=f.category,
                    reason_code=f.reason_code,
                    fact_version_ids=f.fact_version_ids,
                )
                for f in ref.consistency_findings
            ),
            catalogue_fingerprint=ref.catalogue_fingerprint,
        )
    claims = []
    for claim in gate.claims:
        evidence = None
        e = claim.evidence
        if e is not None:
            # Binding/reference types contain only identifiers and hashes.
            evidence = dict(
                binding=e.binding,
                approval_manifest_id=e.approval_manifest_id,
                approval_manifest_version=e.approval_manifest_version,
                approval_manifest_hash=e.approval_manifest_hash,
                integrity=_rule(e.integrity),
                applicability=_rule(e.applicability),
            )
        claims.append(
            dict(
                claim_id=claim.claim_id,
                decision=claim.decision,
                reason_codes=claim.reason_codes,
                grounding=_grounding(claim.grounding),
                evidence=evidence,
                **_checks(claim),
            )
        )
    return AuditMetadata.model_validate(
        dict(
            patient_id=gate.patient_id,
            actor_id=actor_id,
            actor_role=actor_role,
            draft_id=gate.draft_id,
            gate_policy_version=gate.policy_version,
            request_digest=gate.request_digest,
            verification=verification,
            claims=tuple(claims),
            decision=gate.decision,
            reason_codes=gate.reason_codes,
            **_checks(gate),
        )
    )


def event_output(row: VerificationAuditEvent) -> AuditEvent:
    """Validate persisted scope and digest before returning any audit metadata."""
    try:
        metadata = AuditMetadata.model_validate_json(json.dumps(row.payload))
        if (
            metadata.patient_id != row.patient_id
            or metadata.actor_id != row.actor_id
            or metadata.actor_role != row.actor_role
            or metadata.event_kind != row.event_kind
            or metadata.audit_schema_version != row.audit_schema_version
            or metadata.decision != row.gate_decision
            or canonical_hash(metadata.model_dump(mode="json")) != row.payload_hash
        ):
            raise ValueError("Invalid audit identity")
        return AuditEvent(
            id=row.id,
            operation_id=row.operation_id,
            payload_hash=row.payload_hash,
            metadata=metadata,
            evaluated_at=row.evaluated_at,
            recorded_at=row.recorded_at,
        )
    except (ValidationError, ValueError):
        raise HTTPException(503, "Audit history unavailable; rollback required") from None


async def record_patient_gate_evaluation(
    db: AsyncSession,
    account_id: UUID,
    patient_id: UUID,
    original_request: object,
    operation_id: UUID,
) -> AuditEvent:
    """Evaluate independently, append/flush, never commit or call a consumer.

    Gate uses a clean session and retains current authorization/patient locks.
    Caller MUST rollback on any failure and owns commit/lock lifetime on success.
    Flush/receipt is transaction-local, not evidence of durability or delivery.
    """
    run = await evaluate_patient_gate(db, account_id, patient_id, original_request)
    try:
        operation = TypeAdapter(UUID).validate_python(operation_id)
    except ValidationError:
        raise HTTPException(422, "Invalid audit operation identity") from None
    try:
        account = await eligible_account(db, account_id)
        try:
            metadata = project_gate(run.gate, account.id, account.role)
        except ValidationError:
            raise HTTPException(503, "Audit projection unavailable; rollback required") from None
        if metadata.patient_id != patient_id:
            raise HTTPException(409, "Audit scope unavailable; rollback required")
        payload = metadata.model_dump(mode="json")
        digest = canonical_hash(payload)
        existing = await db.scalar(
            select(VerificationAuditEvent)
            .where(
                VerificationAuditEvent.patient_id == patient_id,
                VerificationAuditEvent.actor_id == account.id,
                VerificationAuditEvent.operation_id == operation,
                VerificationAuditEvent.event_kind == "GATE_EVALUATED",
            )
            .execution_options(populate_existing=True)
        )
        if existing is not None:
            previous = event_output(existing)
            if previous.payload_hash != digest or previous.metadata != metadata:
                raise HTTPException(409, "Audit operation conflict; rollback required")
            await authorize_patient_scope(db, account_id, patient_id)
            return previous
        await authorize_patient_scope(db, account_id, patient_id)
        row = VerificationAuditEvent(
            patient_id=patient_id,
            actor_id=account.id,
            actor_role=account.role,
            operation_id=operation,
            event_kind=metadata.event_kind,
            audit_schema_version=metadata.audit_schema_version,
            gate_decision=metadata.decision,
            payload=payload,
            payload_hash=digest,
            evaluated_at=run.evaluated_at,
        )
        db.add(row)
        await db.flush()
        return event_output(row)
    except SQLAlchemyError:
        # Do not hide transaction rollback or convert failure into audit success.
        raise HTTPException(503, "Audit persistence unavailable; rollback required") from None


async def _read_scope(db: AsyncSession, account_id: UUID, patient_id: UUID) -> User:
    if db.new or db.dirty or db.deleted:
        raise HTTPException(409, "Audit retrieval requires a clean session")
    with db.no_autoflush:
        return await lock_patient_scope(db, account_id, patient_id)


async def get_patient_audit_event(
    db: AsyncSession,
    account_id: UUID,
    patient_id: UUID,
    event_id: UUID,
) -> AuditEvent:
    """Own events only; caller retains locks through authorized consumption."""
    account = await _read_scope(db, account_id, patient_id)
    row = await db.scalar(
        select(VerificationAuditEvent)
        .where(
            VerificationAuditEvent.id == event_id,
            VerificationAuditEvent.patient_id == patient_id,
            VerificationAuditEvent.actor_id == account.id,
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise HTTPException(404, "Audit event unavailable")
    result = event_output(row)
    await authorize_patient_scope(db, account_id, patient_id)
    return result


async def list_patient_audit_events(
    db: AsyncSession,
    account_id: UUID,
    patient_id: UUID,
    *,
    limit: int = 50,
) -> tuple[AuditEvent, ...]:
    """Bounded recent metadata, actor-only even with shared-record consent."""
    account = await _read_scope(db, account_id, patient_id)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise HTTPException(422, "Audit limit must be between 1 and 100")
    rows = await db.scalars(
        select(VerificationAuditEvent)
        .where(
            VerificationAuditEvent.patient_id == patient_id,
            VerificationAuditEvent.actor_id == account.id,
        )
        .order_by(VerificationAuditEvent.recorded_at.desc(), VerificationAuditEvent.id.desc())
        .limit(limit)
        .execution_options(populate_existing=True)
    )
    result = tuple(event_output(row) for row in rows)
    await authorize_patient_scope(db, account_id, patient_id)
    return result
