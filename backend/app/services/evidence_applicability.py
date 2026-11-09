"""Internal read-only synthetic applicability under current patient authorization."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.evidence import EvidenceRun, VerificationRequest
from app.services.authorization import authorize_patient_scope
from app.services.consistency import load_accepted_state
from app.services.consistency_evaluator import ConsistencyInputError
from app.services.evidence_catalog import prepare_evidence
from app.services.evidence_evaluator import EvidenceInputError, evaluate_evidence_context


async def evaluate_patient_evidence(
    db: AsyncSession, account_id: UUID, request: VerificationRequest
) -> EvidenceRun:
    """Resolve local patient-independent material before acquiring patient locks.

    No database writes/commit; caller retains transaction through consumption and
    owns cleanup. Future APIs must use the existing protected release gate. A
    returned result is neither a clinical verdict nor a reusable permission grant.
    """
    prepared = prepare_evidence(request)
    current, history = await load_accepted_state(db, account_id, request.patient_id)
    try:
        evaluation = evaluate_evidence_context(request, current, history, prepared)
    except (EvidenceInputError, ConsistencyInputError):
        raise HTTPException(409, "Evidence context unavailable") from None
    # Bounded local recheck: refuse changed/withdrawn trust material before return.
    if prepare_evidence(request) != prepared:
        raise HTTPException(503, "Evidence catalogue changed; retry")
    await authorize_patient_scope(db, account_id, request.patient_id)
    return EvidenceRun(evaluated_at=datetime.now(UTC), evaluation=evaluation)
