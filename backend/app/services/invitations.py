"""Controlled synthetic-user onboarding. No clinical access or consent mutation.

Tokens/codes are high-entropy independent secrets, hashed before DB access.
Never log request bodies, invitation URLs, verification codes or passwords.
"""

import hashlib
import secrets
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.security import get_password_hash
from app.models import PatientInvitation, PatientProfile, PractitionerProfile, User
from app.schemas.invitation import (
    ActivatePatient,
    InvitationCreated,
    InvitationState,
    PatientActivated,
)
from app.services.authorization import eligible_account


def secret_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def unavailable() -> HTTPException:
    # One response for invalid, expired, consumed, revoked and inconsistent links.
    return HTTPException(400, "Invitation unavailable or verification failed")


async def database_time(db: AsyncSession) -> datetime:
    # clock_timestamp advances even while the transaction waits for a row lock.
    value = await db.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime):
        raise unavailable()
    return value


async def issuing_practitioner(db: AsyncSession, account_id: UUID) -> UUID:
    account = await eligible_account(db, account_id)
    if account.role != "practitioner":
        raise HTTPException(403, "Practitioner authorization required")
    profile_id = await db.scalar(
        select(PractitionerProfile.id).where(
            PractitionerProfile.account_id == account.id,
            PractitionerProfile.account_role == "practitioner",
            PractitionerProfile.is_authorized.is_(True),
        )
    )
    if profile_id is None:
        raise HTTPException(403, "Practitioner authorization required")
    return profile_id


async def create_invitation(db: AsyncSession, account_id: UUID, identity: str) -> InvitationCreated:
    issuer_id = await issuing_practitioner(db, account_id)
    if await db.scalar(select(User.id).where(User.identity == identity)) is not None:
        raise HTTPException(409, "Synthetic identity unavailable")
    token, code = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    now = await database_time(db)
    patient = PatientProfile(initiated_by_id=issuer_id)
    db.add(patient)
    await db.flush()
    invitation = PatientInvitation(
        patient_id=patient.id,
        issued_by_id=issuer_id,
        intended_identity=identity,
        token_hash=secret_digest(token),
        verification_hash=secret_digest(code),
        created_at=now,
        expires_at=now + timedelta(minutes=settings.INVITATION_EXPIRE_MINUTES),
    )
    db.add(invitation)
    await db.flush()
    result = InvitationCreated(
        invitation_id=invitation.id,
        patient_id=patient.id,
        invitation_url=f"{settings.INVITATION_URL_ORIGIN}/activate#token={token}",
        prototype_verification_code=code,
        expires_at=invitation.expires_at,
    )
    await db.commit()
    return result


async def valid_invitation(
    db: AsyncSession, token: str, *, lock: bool = False
) -> PatientInvitation:
    if len(token) != 43 or not token.isascii():
        raise unavailable()
    query = select(PatientInvitation).where(PatientInvitation.token_hash == secret_digest(token))
    if lock:
        query = query.with_for_update()
    invitation = await db.scalar(query.execution_options(populate_existing=True))
    if invitation is None:
        raise unavailable()
    now = await database_time(db)
    if (
        invitation.status != "pending"
        or invitation.consumed_at is not None
        or invitation.revoked_at is not None
        or invitation.expires_at <= now
    ):
        raise unavailable()
    # An inactive/deauthorized issuer cannot continue onboarding patients.
    issuer = await db.scalar(
        select(PractitionerProfile.id)
        .join(User, User.id == PractitionerProfile.account_id)
        .where(
            PractitionerProfile.id == invitation.issued_by_id,
            PractitionerProfile.account_role == "practitioner",
            PractitionerProfile.is_authorized.is_(True),
            User.role == "practitioner",
            User.is_active.is_(True),
        )
    )
    if issuer is None:
        raise unavailable()
    return invitation


async def activate_patient(db: AsyncSession, payload: ActivatePatient) -> PatientActivated:
    invitation = await valid_invitation(db, payload.token.get_secret_value(), lock=True)
    if (
        payload.identity.strip().lower() != invitation.intended_identity
        or not secrets.compare_digest(
            secret_digest(payload.verification_code.get_secret_value()),
            invitation.verification_hash,
        )
    ):
        raise unavailable()
    password = payload.password.get_secret_value()
    if not 8 <= len(password) <= 1024:
        raise HTTPException(400, "Password must contain 8 to 1024 characters")
    patient = await db.scalar(
        select(PatientProfile)
        .where(
            PatientProfile.id == invitation.patient_id,
            PatientProfile.initiated_by_id == invitation.issued_by_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        patient is None
        or patient.is_active
        or patient.account_id is not None
        or patient.activated_at is not None
    ):
        raise unavailable()
    password_hash = await run_in_threadpool(get_password_hash, password)
    now = await database_time(db)
    if now >= invitation.expires_at:
        raise unavailable()
    user = User(
        identity=invitation.intended_identity,
        hashed_password=password_hash,
        role="patient",
        is_active=True,
    )
    db.add(user)
    try:
        await db.flush()
        patient.account_id = user.id
        patient.verified_at = patient.activated_at = now
        patient.is_active = True
        invitation.status, invitation.consumed_at = "consumed", now
        await db.flush()
        result = PatientActivated(account_id=user.id, patient_id=patient.id)
        # User, profile activation and consumption commit together before success.
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise unavailable() from None
    return result


async def managed_invitation(
    db: AsyncSession, account_id: UUID, invitation_id: UUID, *, revoke: bool = False
) -> InvitationState:
    issuer_id = await issuing_practitioner(db, account_id)
    query = select(PatientInvitation).where(
        PatientInvitation.id == invitation_id, PatientInvitation.issued_by_id == issuer_id
    )
    if revoke:
        query = query.with_for_update()
    invitation = await db.scalar(query.execution_options(populate_existing=True))
    if invitation is None:
        raise HTTPException(404, "Invitation not found")
    if revoke:
        if invitation.status != "pending":
            raise HTTPException(409, "Invitation is not pending")
        invitation.status, invitation.revoked_at = "revoked", await database_time(db)
        await db.flush()
    result = InvitationState(
        invitation_id=invitation.id,
        patient_id=invitation.patient_id,
        status=invitation.status,
        expires_at=invitation.expires_at,
    )
    if revoke:
        await db.commit()
    return result
