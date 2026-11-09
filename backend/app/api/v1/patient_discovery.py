"""Authenticated own-profile and consent-filtered patient discovery only."""

from fastapi import APIRouter, Response

from app.api.dependencies import CurrentAccount, Database
from app.api.v1.auth import PrivateValidationRoute
from app.schemas.patient_discovery import AccessiblePatient, OwnProfile
from app.services.patient_discovery import accessible_patients, own_profile

router = APIRouter(route_class=PrivateValidationRoute, tags=["discovery"])


@router.get("/auth/profile", response_model=OwnProfile)
async def profile(db: Database, account: CurrentAccount, response: Response) -> OwnProfile:
    result = await own_profile(db, account.id)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/patients", response_model=list[AccessiblePatient])
async def patients(
    db: Database, account: CurrentAccount, response: Response
) -> list[AccessiblePatient]:
    result = await accessible_patients(db, account.id)
    response.headers["Cache-Control"] = "no-store"
    return result
