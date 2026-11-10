"""Explicitly enabled local synthetic bootstrap; never production registration."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.auth import PrivateValidationRoute
from app.core.config import settings
from app.core.database import get_db
from app.provision_demo import insert_practitioner, validate_target


def require_demo_mode(request: Request) -> None:
    try:
        if not (
            settings.APP_ENV == "development"
            and settings.ENABLE_DEMO_REGISTRATION
            and settings.LLM_PROVIDER == "mock"
        ):
            raise ValueError("disabled")
        if request.client is None or request.client.host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("non-local client")
        if request.headers.get("origin") not in {
            None,
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        }:
            raise ValueError("non-local browser origin")
        validate_target(settings.DATABASE_URL)
    except ValueError:
        raise HTTPException(404, "Demo registration unavailable") from None


class DemoRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    identity: str = Field(pattern=r"^[a-z0-9][a-z0-9._+-]{0,63}@synthetic\.invalid$")
    password: SecretStr
    password_confirmation: SecretStr
    acknowledge_unverified_demo: Literal[True]

    @model_validator(mode="after")
    def passwords(self) -> "DemoRegistration":
        value = self.password.get_secret_value()
        if not 8 <= len(value) <= 1024 or value != self.password_confirmation.get_secret_value():
            raise ValueError("Invalid password or confirmation")
        return self


class DemoCreated(BaseModel):
    status: Literal["created"] = "created"
    identity_assurance: Literal["unverified_demo"] = "unverified_demo"


async def registration_database() -> AsyncGenerator[AsyncSession, None]:
    """Reuse the existing transaction owner, sanitizing commit/connection failures."""
    try:
        async with asynccontextmanager(get_db)() as session:
            yield session
    except SQLAlchemyError:
        raise HTTPException(503, "Demo registration could not be confirmed") from None


router = APIRouter(
    route_class=PrivateValidationRoute, dependencies=[Depends(require_demo_mode)], tags=["demo"]
)


@router.get("/auth/demo-registration")
async def availability(response: Response) -> dict[str, bool]:
    response.headers["Cache-Control"] = "no-store"
    return {"enabled": True}


@router.post("/auth/demo-registration", status_code=201, response_model=DemoCreated)
async def register(
    payload: DemoRegistration,
    db: Annotated[AsyncSession, Depends(registration_database, scope="function")],
    response: Response,
) -> DemoCreated:
    # Commit must complete before a success response; failure rolls back both rows.
    # The helper also verifies current_database(), loopback server and server port.
    try:
        await insert_practitioner(
            db, payload.password.get_secret_value(), identity=payload.identity
        )
    except (ValueError, IntegrityError):
        raise HTTPException(409, "Demo account cannot be created") from None
    response.headers["Cache-Control"] = "no-store"
    return DemoCreated()
