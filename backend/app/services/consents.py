"""Patient-controlled consent with serialized mutations and preserved snapshots.

One current row per pair is retained. Only actual transitions append events;
legacy rows receive an explicitly unattributed baseline before any mutation.
Concurrent operations serialize in database lock order, not HTTP arrival order.
An explicit grant ordered after a revoke is a re-grant. This does not establish
transactional safety for future in-flight clinical data release or writes.
"""

from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConsentHistory, PatientProfile, PractitionerProfile, User
from app.models import PatientPractitionerConsent as Consent
from app.schemas.consent import ConsentResponse
from app.services.authorization import authorize_patient_scope, eligible_account


async def own_patient(db: AsyncSession, account_id: UUID, *, lock: bool = False) -> UUID:
    account = await eligible_account(db, account_id)
    if account.role != "patient":
        raise HTTPException(403, "Patient consent management required")
    query = (
        select(PatientProfile.id)
        .join(User, User.id == PatientProfile.account_id)
        .where(
            PatientProfile.account_id == account_id,
            PatientProfile.account_role == "patient",
            User.role == "patient",
            User.is_active.is_(True),
        )
    )
    if lock:
        # This also protects the absent-pair case; no UPSERT can lose history.
        query = query.with_for_update(of=[PatientProfile, User])
    patient_id = await db.scalar(query)
    if patient_id is None:
        raise HTTPException(403, "Patient consent management denied")
    # Reuse the existing fail-closed patient scope policy after obtaining locks.
    return await authorize_patient_scope(db, account_id, patient_id)


def response(consent: Consent) -> ConsentResponse:
    return ConsentResponse(
        consent_id=consent.id,
        practitioner_id=consent.practitioner_id,
        status=consent.status,
        granted_at=consent.granted_at,
        revoked_at=consent.revoked_at,
    )


async def list_consents(db: AsyncSession, account_id: UUID) -> list[ConsentResponse]:
    patient_id = await own_patient(db, account_id)
    rows = await db.scalars(
        select(Consent)
        .where(Consent.patient_id == patient_id)
        .order_by(Consent.practitioner_id)
        .execution_options(populate_existing=True)
    )
    return [response(row) for row in rows]


async def clock(db: AsyncSession) -> datetime:
    now = await db.scalar(select(func.clock_timestamp()))
    if not isinstance(now, datetime):
        raise HTTPException(409, "Consent state unavailable")
    return now


def append_snapshot(
    db: AsyncSession,
    consent: Consent,
    version: int,
    now: datetime,
    actor_id: UUID | None,
    kind: str,
) -> None:
    db.add(
        ConsentHistory(
            consent_id=consent.id,
            version=version,
            actor_id=actor_id,
            kind=kind,
            status=consent.status,
            granted_at=consent.granted_at,
            revoked_at=consent.revoked_at,
            recorded_at=now,
        )
    )


async def preserve_current(db: AsyncSession, consent: Consent, now: datetime) -> int:
    if (
        consent.patient_active is not True
        or consent.granted_at > now
        or consent.status not in {"granted", "revoked"}
        or (consent.status == "granted" and consent.revoked_at is not None)
        or (
            consent.status == "revoked"
            and (
                consent.revoked_at is None
                or consent.revoked_at < consent.granted_at
                or consent.revoked_at > now
            )
        )
    ):
        raise HTTPException(409, "Inconsistent consent state")
    previous = await db.scalar(
        select(ConsentHistory)
        .where(ConsentHistory.consent_id == consent.id)
        .order_by(ConsentHistory.version.desc())
        .limit(1)
    )
    if previous is None:
        append_snapshot(db, consent, 1, now, None, "baseline")
        return 1
    if (
        previous.status != consent.status
        or previous.granted_at != consent.granted_at
        or previous.revoked_at != consent.revoked_at
    ):
        raise HTTPException(409, "Consent history mismatch")
    return previous.version


async def change_consent(
    db: AsyncSession, account_id: UUID, practitioner_id: UUID, *, grant: bool
) -> ConsentResponse:
    patient_id = await own_patient(db, account_id, lock=True)
    if grant:
        # Explicit prototype eligibility; role or consent alone is insufficient.
        # Lock eligibility rows until commit, so a concurrent disabling update
        # cannot overtake a successful grant's eligibility check.
        eligible = await db.scalar(
            select(PractitionerProfile.id)
            .join(User, User.id == PractitionerProfile.account_id)
            .where(
                PractitionerProfile.id == practitioner_id,
                PractitionerProfile.account_role == "practitioner",
                PractitionerProfile.is_authorized.is_(True),
                User.role == "practitioner",
                User.is_active.is_(True),
            )
            .with_for_update(of=[PractitionerProfile, User])
        )
        if eligible is None:
            raise HTTPException(403, "Eligible practitioner required")
    # Revocation deliberately does not require continuing practitioner eligibility.
    consent = await db.scalar(
        select(Consent)
        .where(
            Consent.patient_id == patient_id,
            Consent.practitioner_id == practitioner_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = await clock(db)
    if consent is None:
        if not grant:
            raise HTTPException(404, "Consent relationship not found")
        consent = Consent(
            patient_id=patient_id, practitioner_id=practitioner_id, status="granted", granted_at=now
        )
        db.add(consent)
        await db.flush()
        append_snapshot(db, consent, 1, now, account_id, "granted")
    else:
        version = await preserve_current(db, consent, now)
        desired = "granted" if grant else "revoked"
        if consent.status != desired:
            consent.status = desired
            if grant:
                consent.granted_at, consent.revoked_at = now, None
            else:
                consent.revoked_at = now
            append_snapshot(db, consent, version + 1, now, account_id, desired)
    await db.flush()
    result = response(consent)
    # Current state and its history become visible together, before success.
    await db.commit()
    return result
