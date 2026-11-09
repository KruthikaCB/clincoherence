"""Read-only advisory discovery; protected operations must authorize independently."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import PatientPractitionerConsent as Consent
from app.models import PatientProfile, PractitionerProfile, User
from app.schemas.patient_discovery import AccessiblePatient, OwnProfile
from app.services.authorization import (
    authentication_failed,
    authorize_patient_scope,
    eligible_account,
)


async def own_profile(db: AsyncSession, account_id: UUID) -> OwnProfile:
    account = await eligible_account(db, account_id)
    if account.role == "patient":
        profile_id = await db.scalar(
            select(PatientProfile.id).where(
                PatientProfile.account_id == account_id,
                PatientProfile.account_role == "patient",
            )
        )
        if profile_id is not None:
            await authorize_patient_scope(db, account_id, profile_id)
    else:
        profile_id = await db.scalar(
            select(PractitionerProfile.id).where(
                PractitionerProfile.account_id == account_id,
                PractitionerProfile.account_role == "practitioner",
                PractitionerProfile.is_authorized.is_(True),
            )
        )
    if profile_id is None:
        raise authentication_failed()
    # Recheck eligibility instead of treating a previous JWT/ORM lookup as authority.
    account = await eligible_account(db, account_id)
    return OwnProfile(role=account.role, profile_id=profile_id)


async def accessible_patients(db: AsyncSession, account_id: UUID) -> list[AccessiblePatient]:
    account = await eligible_account(db, account_id)
    if account.role != "practitioner":
        raise HTTPException(403, "Practitioner authorization required")
    patient_account, practitioner_account = aliased(User), aliased(User)
    now = datetime.now(UTC)
    # Select UUIDs only; never load invitations, identities or clinical payloads.
    candidates = await db.scalars(
        select(PatientProfile.id)
        .join(patient_account, patient_account.id == PatientProfile.account_id)
        .join(Consent, Consent.patient_id == PatientProfile.id)
        .join(PractitionerProfile, PractitionerProfile.id == Consent.practitioner_id)
        .join(practitioner_account, practitioner_account.id == PractitionerProfile.account_id)
        .where(
            PractitionerProfile.account_id == account_id,
            PractitionerProfile.account_role == "practitioner",
            PractitionerProfile.is_authorized.is_(True),
            practitioner_account.role == "practitioner",
            practitioner_account.is_active.is_(True),
            PatientProfile.account_role == "patient",
            PatientProfile.is_active.is_(True),
            PatientProfile.verified_at.is_not(None),
            PatientProfile.activated_at.is_not(None),
            PatientProfile.verified_at <= PatientProfile.activated_at,
            PatientProfile.activated_at <= now,
            patient_account.role == "patient",
            patient_account.is_active.is_(True),
            Consent.patient_active.is_(True),
            Consent.status == "granted",
            Consent.revoked_at.is_(None),
            Consent.granted_at.is_not(None),
            Consent.granted_at <= now,
        )
        .order_by(PatientProfile.id)
    )
    results = []
    for patient_id in candidates:
        try:
            # Retain the authoritative policy, including changes since candidate selection.
            await authorize_patient_scope(db, account_id, patient_id)
        except HTTPException as error:
            if error.status_code != 403:
                raise
            continue  # No disclosure of why a candidate is absent.
        results.append(AccessiblePatient(patient_id=patient_id))
    await eligible_account(db, account_id)
    return results
