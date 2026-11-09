"""Patient-locked internal evaluation and synchronous protected consumption."""

from collections.abc import Callable
from datetime import UTC, datetime
from inspect import iscoroutinefunction
from typing import TypeVar
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.evidence_aware_draft import (
    EvidenceAwareDraftRequest,
    EvidenceAwareRecord,
    EvidenceAwareRun,
)
from app.services.authorization import authorize_patient_scope
from app.services.consistency import load_accepted_state
from app.services.consistency_evaluator import ConsistencyInputError
from app.services.draft_verifier import DraftInputError
from app.services.evidence_aware_evaluator import (
    catalogue_fingerprint,
    evaluate_evidence_aware_draft,
    prepare_batch,
)
from app.services.evidence_evaluator import EvidenceInputError

T = TypeVar("T")


async def verify_patient_evidence_aware_draft(
    db: AsyncSession, account_id: UUID, patient_id: UUID, draft: object
) -> EvidenceAwareRun:
    """No writes/commit. Caller owns cleanup and retains locks through use.

    Malformed input errors are sanitized. No provider data is logged. An
    evaluation record itself is not a transferable authorization capability.
    """
    try:
        request = EvidenceAwareDraftRequest.model_validate(
            draft.model_dump(warnings=False)
            if isinstance(draft, EvidenceAwareDraftRequest)
            else draft
        )
    except ValidationError:
        # Authenticate before returning request-shape errors, as in 4D.
        await load_accepted_state(db, account_id, patient_id)
        raise HTTPException(422, "Invalid evidence-aware draft request") from None
    prepared = prepare_batch(request)  # local evidence work before patient locks
    current, history = await load_accepted_state(db, account_id, patient_id)
    try:
        record = evaluate_evidence_aware_draft(patient_id, current, history, request, prepared)
    except (DraftInputError, ConsistencyInputError, EvidenceInputError):
        raise HTTPException(409, "Draft accepted context unavailable") from None
    await authorize_patient_scope(db, account_id, patient_id)
    if catalogue_fingerprint() != prepared.fingerprint or prepared.fingerprint is None:
        raise HTTPException(503, "Evidence catalogue unavailable; reevaluate")
    return EvidenceAwareRun(evaluated_at=datetime.now(UTC), verification=record)


async def consume_patient_evidence_aware_draft(
    db: AsyncSession,
    account_id: UUID,
    patient_id: UUID,
    draft: object,
    expected: EvidenceAwareRecord,
    consumer: Callable[[EvidenceAwareRecord], T],
) -> T:
    """Reevaluate delayed work under current authorization and patient locks.

    Trusted consumer must be synchronous, bounded, non-streaming and must not
    defer use, release locks or retain text for later disclosure. Not an HTTP
    release mechanism. No await occurs between the last catalogue check and
    invoking consumer. Frozen per-deployment assets are a required assumption;
    cross-worker/deployment atomic withdrawal is explicitly not provided.
    """
    if iscoroutinefunction(consumer) or iscoroutinefunction(type(consumer).__call__):
        raise TypeError("Protected internal consumer must be synchronous")
    run = await verify_patient_evidence_aware_draft(db, account_id, patient_id, draft)
    if run.verification != expected:
        raise HTTPException(409, "Verification context changed; reevaluate")
    if catalogue_fingerprint() != run.verification.catalogue_fingerprint:
        raise HTTPException(503, "Evidence catalogue changed; reevaluate")
    return consumer(run.verification)
