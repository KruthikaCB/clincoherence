"""Read-only patient-scoped gate; 4E owns authorization and consumption locks."""

from collections.abc import Callable
from datetime import UTC, datetime
from inspect import iscoroutinefunction
from typing import TypeVar
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.evidence_aware_draft import EvidenceAwareDraftRequest, EvidenceAwareRecord
from app.schemas.verification_gate import GateDecisionRecord, GateInput, GateRun
from app.services.evidence_aware_draft import (
    consume_patient_evidence_aware_draft,
    verify_patient_evidence_aware_draft,
)
from app.services.gate_evaluator import GateInputError, evaluate_gate

T = TypeVar("T")


def _original_request(draft: object) -> EvidenceAwareDraftRequest:
    # Freeze provider data locally; shape errors are disclosed only after 4E
    # authorization by _verify_original.
    try:
        return EvidenceAwareDraftRequest.model_validate(
            draft.model_dump(warnings=False)
            if isinstance(draft, EvidenceAwareDraftRequest)
            else draft
        )
    except ValidationError:
        raise HTTPException(422, "Invalid original gate request") from None


def _evaluate(
    request: EvidenceAwareDraftRequest, record: EvidenceAwareRecord
) -> GateDecisionRecord:
    try:
        return evaluate_gate(GateInput(request=request, verification=record))
    except GateInputError:
        raise HTTPException(409, "Gate context unavailable") from None


async def _verify_original(
    db: AsyncSession,
    account_id: UUID,
    patient_id: UUID,
    draft: object,
) -> tuple[EvidenceAwareDraftRequest, EvidenceAwareRecord]:
    # Freeze one complete envelope before awaiting database work. A mutable
    # caller dict must not drop prose/claims between verification and gating.
    try:
        request = _original_request(draft)
    except HTTPException:
        # Preserve 4E's authorization-first error disclosure and clean-session
        # behavior for malformed requests, rather than returning a shape oracle.
        await verify_patient_evidence_aware_draft(db, account_id, patient_id, draft)
        raise
    verified = await verify_patient_evidence_aware_draft(db, account_id, patient_id, request)
    return request, verified.verification


async def evaluate_patient_gate(
    db: AsyncSession,
    account_id: UUID,
    patient_id: UUID,
    draft: object,
) -> GateRun:
    """No provider-supplied verification or decisions accepted here.

    Caller owns cleanup and retains patient locks. No original/4E text is exposed
    by a failed gate; only PASS contains the complete server-rendered record set.
    """
    request, verified = await _verify_original(db, account_id, patient_id, draft)
    result = _evaluate(request, verified)
    return GateRun(evaluated_at=datetime.now(UTC), gate=result)


async def consume_patient_gate(
    db: AsyncSession,
    account_id: UUID,
    patient_id: UUID,
    draft: object,
    expected: GateDecisionRecord,
    consumer: Callable[[GateDecisionRecord], T],
) -> T:
    """Fresh 4E evaluation plus its existing protected synchronous callback.

    Consumer is trusted, bounded, non-streaming, and must not defer disclosure,
    retain text for later release or release the transaction locks. No HTTP
    release authorization is granted. Old PASS records are never capabilities.
    """
    if iscoroutinefunction(consumer) or iscoroutinefunction(type(consumer).__call__):
        raise TypeError("Protected gate consumer must be synchronous")
    request, verified = await _verify_original(db, account_id, patient_id, draft)

    def guarded(record: EvidenceAwareRecord) -> T:
        gate = _evaluate(request, record)
        if gate != expected:
            raise HTTPException(409, "Gate context changed; reevaluate")
        if gate.decision != "PASS":
            raise HTTPException(409, "Gate withheld batch")
        return consumer(gate)

    return await consume_patient_evidence_aware_draft(
        db,
        account_id,
        patient_id,
        request,
        verified,
        guarded,
    )
