"""Local-only synthetic patient creation; never fabricated patient consent."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentAccount, Database
from app.api.v1.auth import PrivateValidationRoute
from app.api.v1.demo_registration import registration_database
from app.core.demo_patients import enabled, verified_database
from app.models import PatientProfile, PractitionerProfile, User
from app.services.authorization import eligible_account
from app.services.patient_discovery import accessible_patients


def require_demo_patients(request: Request) -> None:
    if (
        not enabled()
        or request.client is None
        or request.client.host not in {"127.0.0.1", "::1", "localhost"}
        or request.headers.get("origin")
        not in {None, "http://localhost:3000", "http://127.0.0.1:3000"}
    ):
        raise HTTPException(404, "Demo patients unavailable")


class CreateDemoPatient(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    identifier: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")


class DemoPatient(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    patient_id: UUID
    identifier: str
    label: Literal["Synthetic Demo Patient"] = "Synthetic Demo Patient"


router = APIRouter(
    route_class=PrivateValidationRoute, dependencies=[Depends(require_demo_patients)], tags=["demo"]
)


@router.get("/demo-patients", response_model=list[DemoPatient])
async def list_demo_patients(
    account: CurrentAccount, db: Database, response: Response
) -> list[DemoPatient]:
    allowed = await accessible_patients(db, account.id)
    rows = await db.scalars(
        select(PatientProfile)
        .where(
            PatientProfile.id.in_([item.patient_id for item in allowed]),
            PatientProfile.is_demo.is_(True),
        )
        .order_by(PatientProfile.id)
    )
    response.headers["Cache-Control"] = "no-store"
    return [DemoPatient(patient_id=row.id, identifier=row.demo_identifier or "") for row in rows]


@router.post("/demo-patients", status_code=201, response_model=DemoPatient)
async def create_demo_patient(
    payload: CreateDemoPatient,
    account: CurrentAccount,
    db: Annotated[AsyncSession, Depends(registration_database, scope="function")],
    response: Response,
) -> DemoPatient:
    if not await verified_database(db):
        raise HTTPException(403, "Demo patient creation denied")
    account = await eligible_account(db, account.id)
    if account.role != "practitioner" or not account.identity.endswith("@synthetic.invalid"):
        raise HTTPException(403, "Demo practitioner required")
    await db.execute(text("SET LOCAL lock_timeout = '5s'"))
    owner = await db.scalar(
        select(PractitionerProfile.id)
        .join(User, User.id == PractitionerProfile.account_id)
        .where(
            PractitionerProfile.account_id == account.id,
            PractitionerProfile.account_role == "practitioner",
            PractitionerProfile.is_authorized.is_(True),
            User.is_active.is_(True),
            User.role == "practitioner",
        )
        .with_for_update(of=[PractitionerProfile, User])
    )
    if owner is None:
        raise HTTPException(403, "Demo practitioner required")
    patient = PatientProfile(
        is_demo=True,
        demo_owner_id=owner,
        initiated_by_id=owner,
        demo_identifier=payload.identifier,
        is_active=True,
    )
    db.add(patient)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(
            409, "Demo identifier unavailable; existing patient unchanged"
        ) from None
    response.headers["Cache-Control"] = "no-store"
    return DemoPatient(patient_id=patient.id, identifier=payload.identifier)
