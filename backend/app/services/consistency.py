"""Patient-locked read-only adapter. Caller owns transaction and data release."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ClinicalFactVersion
from app.schemas.consistency import ConsistencyRun, FactVersion
from app.services.authorization import authorize_patient_scope
from app.services.clinical_facts import current_query, lock_patient_scope
from app.services.consistency_evaluator import ConsistencyInputError, evaluate_consistency


def version_input(row: ClinicalFactVersion) -> FactVersion:
    return FactVersion.model_validate(
        {
            "version_id": row.id,
            "fact_id": row.fact_id,
            "patient_id": row.patient_id,
            "version": row.version,
            "previous_version_id": row.previous_version_id,
            "previous_version": row.previous_version,
            "attribution": row.attribution,
            "author_id": row.author_id,
            "author_role": row.author_role,
            "fact": row.payload,
            "provenance": row.provenance,
            "observed_at": row.observed_at,
            "entered_at": row.entered_at,
            "correction_reason": row.correction_reason,
            "entry_method": row.entry_method,
            "status": row.status,
            "confirmed_by_id": row.confirmed_by_id,
            "confirmed_at": row.confirmed_at,
        }
    )


async def check_patient_consistency(
    db: AsyncSession, account_id: UUID, patient_id: UUID
) -> ConsistencyRun:
    """No commit, insert or audit write. Locks remain held on success.

    As with accepted_facts, caller must retain the transaction through protected
    consumption/ASGI handoff, using the existing release gate for any future API.
    Committing then releasing this result without fresh authorization is unsafe.
    This service does not provide a public endpoint or permission cache.
    """
    if db.new or db.dirty or db.deleted:
        raise HTTPException(409, "Consistency evaluation requires a clean session")
    with db.no_autoflush:
        await lock_patient_scope(db, account_id, patient_id)
        history = list(
            await db.scalars(
                select(ClinicalFactVersion).where(
                    ClinicalFactVersion.patient_id == patient_id,
                    ClinicalFactVersion.status == "CONFIRMED",
                )
            )
        )
        current = list(await db.scalars(current_query(patient_id)))
        try:
            evaluation = evaluate_consistency(
                patient_id, [version_input(r) for r in current], [version_input(r) for r in history]
            )
        except (ValidationError, ConsistencyInputError):
            raise HTTPException(
                409, "Accepted state unavailable for consistency evaluation"
            ) from None
        await authorize_patient_scope(db, account_id, patient_id)
        return ConsistencyRun(evaluated_at=datetime.now(UTC), evaluation=evaluation)
