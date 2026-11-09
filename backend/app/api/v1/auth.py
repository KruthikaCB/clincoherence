"""Login for existing eligible accounts; no registration or consent mutations."""

from collections.abc import Callable, Coroutine
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import (
    CurrentAccount,
    Database,
    patient_scope,
    require_signing_configuration,
)
from app.core.security import create_access_token, get_password_hash, verify_password
from app.models import User
from app.schemas.auth import AccountResponse, LoginRequest, PatientScopeResponse, TokenResponse
from app.services.authorization import authentication_failed, eligible_account


class PrivateValidationRoute(APIRoute):
    """Do not echo submitted credentials in request validation errors."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def private_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(422, "Invalid authentication or scope request") from None

        return private_handler


router = APIRouter(route_class=PrivateValidationRoute)
# Used for unknown identities so login still performs a password hash check.
_dummy_hash = get_password_hash("unused-synthetic-login-timing-value")


@router.post("/auth/login", response_model=TokenResponse, tags=["auth"])
async def login(payload: LoginRequest, db: Database, response: Response) -> TokenResponse:
    require_signing_configuration()
    password = payload.password.get_secret_value()
    if not password or len(password) > 1024:
        raise authentication_failed()
    account = await db.scalar(select(User).where(User.identity == payload.identity.strip().lower()))
    valid = await run_in_threadpool(
        verify_password,
        password,
        account.hashed_password if account else _dummy_hash,
    )
    if not valid or account is None:
        raise authentication_failed()
    account = await eligible_account(db, account.id)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return TokenResponse(access_token=create_access_token(str(account.id)))


@router.get("/auth/me", response_model=AccountResponse, tags=["auth"])
async def me(account: CurrentAccount, response: Response) -> AccountResponse:
    response.headers["Cache-Control"] = "no-store"
    return AccountResponse(id=account.id, role=account.role)


@router.get("/patients/{patient_id}/scope", response_model=PatientScopeResponse, tags=["patients"])
async def scope(
    authorized_patient_id: Annotated[UUID, Depends(patient_scope)],
    response: Response,
) -> PatientScopeResponse:
    # Identity-only demo. Future clinical release/write requires another current
    # consent check coordinated with concurrent revocation, not this result alone.
    response.headers["Cache-Control"] = "no-store"
    return PatientScopeResponse(patient_id=authorized_patient_id)
