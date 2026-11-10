"""Database-backed eligibility and patient scope, with no consent in JWT claims.

This is a point-in-time authorization check. Future record writes and data
release must recheck access and coordinate with revocation transactionally.
The identity-only demo does not prove in-flight clinical data release safety.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.demo_patients import verified_database
from app.models import PatientPractitionerConsent as Consent
from app.models import PatientProfile, PractitionerProfile, User


def authentication_failed() -> HTTPException:
    return HTTPException(
        401, "Invalid credentials or inactive account", headers={"WWW-Authenticate": "Bearer"}
    )


async def eligible_account(db: AsyncSession, account_id: UUID) -> User:
    """Reload current account/profile eligibility, including for existing JWTs."""
    account = await db.scalar(
        select(User).where(User.id == account_id).execution_options(populate_existing=True)
    )
    if account is None or not account.is_active:
        raise authentication_failed()
    if account.role == "patient":
        now = datetime.now(UTC)
        profile_id = await db.scalar(
            select(PatientProfile.id).where(
                PatientProfile.account_id == account.id,
                PatientProfile.account_role == "patient",
                PatientProfile.is_active.is_(True),
                PatientProfile.verified_at.is_not(None),
                PatientProfile.activated_at.is_not(None),
                PatientProfile.verified_at <= PatientProfile.activated_at,
                PatientProfile.activated_at <= now,
            )
        )
    elif account.role == "practitioner":
        # Explicit prototype eligibility, NOT production credential assurance.
        profile_id = await db.scalar(
            select(PractitionerProfile.id).where(
                PractitionerProfile.account_id == account.id,
                PractitionerProfile.account_role == "practitioner",
                PractitionerProfile.is_authorized.is_(True),
            )
        )
    else:
        profile_id = None
    if profile_id is None:
        raise authentication_failed()
    return account


async def authorize_patient_scope(db: AsyncSession, account_id: UUID, patient_id: UUID) -> UUID:
    """Fail closed for absent/inconsistent scope, consent or eligibility.

    Call again at any future sensitive release/write boundary. Do not cache this
    result as a permission grant across requests or long-running operations.
    """
    account = await eligible_account(db, account_id)
    now = datetime.now(UTC)
    query = (
        select(PatientProfile.id)
        .join(User, User.id == PatientProfile.account_id)
        .where(
            PatientProfile.id == patient_id,
            PatientProfile.is_demo.is_(False),
            PatientProfile.account_role == "patient",
            PatientProfile.is_active.is_(True),
            PatientProfile.verified_at.is_not(None),
            PatientProfile.activated_at.is_not(None),
            PatientProfile.verified_at <= PatientProfile.activated_at,
            PatientProfile.activated_at <= now,
            User.role == "patient",
            User.is_active.is_(True),
        )
    )
    if account.role == "patient":
        query = query.where(PatientProfile.account_id == account.id)
    elif account.role == "practitioner":
        query = (
            query.join(Consent, Consent.patient_id == PatientProfile.id)
            .join(PractitionerProfile, PractitionerProfile.id == Consent.practitioner_id)
            .where(
                PractitionerProfile.account_id == account.id,
                PractitionerProfile.account_role == "practitioner",
                PractitionerProfile.is_authorized.is_(True),
                Consent.patient_active.is_(True),
                Consent.status == "granted",
                Consent.revoked_at.is_(None),
                Consent.granted_at.is_not(None),
                Consent.granted_at <= now,
            )
        )
    else:
        raise HTTPException(403, "Patient scope denied")
    result = await db.scalar(query)
    if result is None and account.role == "practitioner" and await verified_database(db):
        result = await db.scalar(
            select(PatientProfile.id)
            .join(PractitionerProfile, PractitionerProfile.id == PatientProfile.demo_owner_id)
            .where(
                PatientProfile.id == patient_id,
                PatientProfile.is_demo.is_(True),
                PatientProfile.is_active.is_(True),
                PatientProfile.account_id.is_(None),
                PatientProfile.verified_at.is_(None),
                PatientProfile.activated_at.is_(None),
                PatientProfile.initiated_by_id == PatientProfile.demo_owner_id,
                PractitionerProfile.account_id == account_id,
                PractitionerProfile.account_role == "practitioner",
                PractitionerProfile.is_authorized.is_(True),
            )
        )
    if result is None:
        # Do not disclose whether an unrelated patient exists.
        raise HTTPException(403, "Patient scope denied")
    return result
