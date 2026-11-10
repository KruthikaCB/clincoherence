"""Structured entry and accepted-state history; no LLM extraction or confirmation API."""

from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import exists, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql import Select

from app.models import ClinicalFactVersion, PatientProfile, PractitionerProfile, User
from app.schemas.clinical_fact import CorrectFact, CreateFact, FactResponse
from app.services.authorization import authorize_patient_scope, eligible_account


async def lock_patient_scope(db: AsyncSession, account_id: UUID, patient_id: UUID) -> User:
    """Same row-lock order as consent mutation, followed by fresh authorization.

    Caller must retain this transaction through read handoff or write commit.
    Do not hold this lock over external evidence/LLM calls.
    """
    await authorize_patient_scope(db, account_id, patient_id)
    try:
        await db.execute(text("SET LOCAL lock_timeout = '5s'"))
        demo = await db.scalar(
            select(PatientProfile.is_demo).where(PatientProfile.id == patient_id)
        )
        lock_query = select(PatientProfile.id).where(PatientProfile.id == patient_id)
        if demo is True:
            lock_query = lock_query.with_for_update(of=PatientProfile)
        else:
            lock_query = lock_query.join(
                User, User.id == PatientProfile.account_id
            ).with_for_update(of=[PatientProfile, User])
        found = await db.scalar(lock_query)
        if found is None:
            raise HTTPException(403, "Patient scope denied")
        account = await eligible_account(db, account_id)
        if account.role == "practitioner":
            # Follow the same owner-then-practitioner lock order as consent grants.
            # Keep account eligibility stable through commit/body handoff too.
            practitioner_id = await db.scalar(
                select(PractitionerProfile.id)
                .join(User, User.id == PractitionerProfile.account_id)
                .where(
                    PractitionerProfile.account_id == account_id,
                    PractitionerProfile.account_role == "practitioner",
                    PractitionerProfile.is_authorized.is_(True),
                    User.role == "practitioner",
                    User.is_active.is_(True),
                )
                .with_for_update(of=[PractitionerProfile, User])
            )
            if practitioner_id is None:
                raise HTTPException(403, "Patient scope denied")
        await authorize_patient_scope(db, account_id, patient_id)
        return await eligible_account(db, account_id)
    except DBAPIError:
        await db.rollback()
        raise HTTPException(503, "Clinical operation unavailable; retry") from None


def output(row: ClinicalFactVersion) -> FactResponse:
    # Validate persisted payload/provenance before any clinical response release.
    values = {
        "fact_id": row.fact_id,
        "version_id": row.id,
        "version": row.version,
        "previous_version_id": row.previous_version_id,
        "patient_id": row.patient_id,
        "attribution": row.attribution,
        "author_id": row.author_id,
        "author_role": row.author_role,
        "fact": row.payload,
        "provenance": row.provenance,
        "observed_at": row.observed_at,
        "entered_at": row.entered_at,
        "entry_method": row.entry_method,
        "status": row.status,
        "correction_reason": row.correction_reason,
        "confirmed_by_id": row.confirmed_by_id,
        "confirmed_at": row.confirmed_at,
    }
    try:
        return FactResponse.model_validate(values)
    except ValidationError:
        raise HTTPException(500, "Stored clinical fact failed validation") from None


def current_query(patient_id: UUID) -> Select[ClinicalFactVersion]:
    newer = aliased(ClinicalFactVersion)
    return select(ClinicalFactVersion).where(
        ClinicalFactVersion.patient_id == patient_id,
        ClinicalFactVersion.status == "CONFIRMED",
        ~exists(
            select(newer.id).where(
                newer.fact_id == ClinicalFactVersion.fact_id,
                newer.patient_id == patient_id,
                newer.status == "CONFIRMED",
                newer.version > ClinicalFactVersion.version,
            )
        ),
    )


async def accepted_facts(
    db: AsyncSession, account_id: UUID, patient_id: UUID
) -> list[FactResponse]:
    await lock_patient_scope(db, account_id, patient_id)
    rows = await db.scalars(
        current_query(patient_id).order_by(
            ClinicalFactVersion.observed_at, ClinicalFactVersion.fact_id
        )
    )
    return [output(row) for row in rows]


async def fact_history(
    db: AsyncSession, account_id: UUID, patient_id: UUID, fact_id: UUID
) -> list[FactResponse]:
    await lock_patient_scope(db, account_id, patient_id)
    rows = list(
        await db.scalars(
            select(ClinicalFactVersion)
            .where(
                ClinicalFactVersion.patient_id == patient_id,
                ClinicalFactVersion.fact_id == fact_id,
                ClinicalFactVersion.status == "CONFIRMED",
            )
            .order_by(ClinicalFactVersion.version)
        )
    )
    if not rows:
        raise HTTPException(404, "Accepted fact not found")
    return [output(row) for row in rows]


async def structured_entry(
    db: AsyncSession,
    account: User,
    patient_id: UUID,
    payload: CreateFact,
    *,
    fact_id: UUID | None = None,
) -> FactResponse:
    """Authorize under the consent-coordinating lock and commit before success."""
    account = await lock_patient_scope(db, account.id, patient_id)
    attribution = "patient_reported" if account.role == "patient" else "practitioner_managed"
    previous = None
    version = 1
    if fact_id is not None:
        if not isinstance(payload, CorrectFact):
            raise HTTPException(422, "Correction details required")
        previous = await db.scalar(
            current_query(patient_id).where(ClinicalFactVersion.fact_id == fact_id)
        )
        if previous is None:
            raise HTTPException(404, "Accepted fact not found")
        if previous.attribution != attribution:
            raise HTTPException(403, "Cannot correct another information category")
        if previous.id != payload.expected_version_id:
            raise HTTPException(409, "Fact version changed; reload before correcting")
        maximum = await db.scalar(
            select(func.max(ClinicalFactVersion.version)).where(
                ClinicalFactVersion.fact_id == fact_id, ClinicalFactVersion.patient_id == patient_id
            )
        )
        version = (maximum or 0) + 1
    row_id = uuid4()
    row = ClinicalFactVersion(
        id=row_id,
        fact_id=fact_id or row_id,
        patient_id=patient_id,
        version=version,
        previous_version_id=previous.id if previous else None,
        previous_version=previous.version if previous else None,
        attribution=attribution,
        author_id=account.id,
        author_role=account.role,
        payload=payload.fact.model_dump(mode="json"),
        provenance=payload.provenance.model_dump(mode="json"),
        observed_at=payload.observed_at,
        correction_reason=payload.correction_reason if isinstance(payload, CorrectFact) else None,
        entry_method="STRUCTURED",
        status="CONFIRMED",
    )
    db.add(row)
    try:
        # Recheck while retaining the consent-coordinating patient lock.
        await authorize_patient_scope(db, account.id, patient_id)
        await db.flush()
        result = output(row)
        await db.commit()
    except DBAPIError:
        await db.rollback()
        raise HTTPException(409, "Clinical write conflict; reload before retrying") from None
    return result
