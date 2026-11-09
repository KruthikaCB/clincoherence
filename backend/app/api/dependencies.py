"""Reusable account authentication and patient-scope authorization dependencies."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError  # type: ignore[import-untyped]
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_access_token, signing_key
from app.models import User
from app.services.authorization import (
    authentication_failed,
    authorize_patient_scope,
    eligible_account,
)

Database = Annotated[AsyncSession, Depends(get_db)]
bearer = HTTPBearer(auto_error=False)


def require_signing_configuration() -> None:
    try:
        signing_key()
    except ValueError:
        raise HTTPException(503, "Authentication signing configuration unavailable") from None


async def current_account(
    db: Database,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    if credentials is None:
        raise authentication_failed()
    require_signing_configuration()
    try:
        account_id = decode_access_token(credentials.credentials)
    except (JWTError, ValueError, TypeError, KeyError):
        raise authentication_failed() from None
    return await eligible_account(db, account_id)


CurrentAccount = Annotated[User, Depends(current_account)]


async def patient_scope(patient_id: UUID, db: Database, account: CurrentAccount) -> UUID:
    return await authorize_patient_scope(db, account.id, patient_id)
