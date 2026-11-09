"""Read-only laboratory service; reuses the 4A locked accepted snapshot."""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.laboratory_comparison import LaboratoryRun, VersionPair
from app.services.authorization import authorize_patient_scope
from app.services.consistency import load_accepted_state
from app.services.consistency_evaluator import ConsistencyInputError
from app.services.laboratory_evaluator import LaboratoryInputError, evaluate_laboratory_pairs


async def compare_patient_laboratories(
    db: AsyncSession, account_id: UUID, patient_id: UUID, pairs: Sequence[VersionPair]
) -> LaboratoryRun:
    """Caller retains locks/transaction through protected consumption and cleanup.

    No flush, commit, mutation or durable audit. A future API must use the existing
    final release gate, never release a cached result under a stale permission.
    """
    current, history = await load_accepted_state(db, account_id, patient_id)
    try:
        evaluation = evaluate_laboratory_pairs(patient_id, current, history, pairs)
    except (LaboratoryInputError, ConsistencyInputError):
        raise HTTPException(409, "Laboratory comparison unavailable") from None
    await authorize_patient_scope(db, account_id, patient_id)
    return LaboratoryRun(evaluated_at=datetime.now(UTC), evaluation=evaluation)
