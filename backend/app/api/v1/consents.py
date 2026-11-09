"""Own-patient consent management; no practitioner-initiated mutations."""

from uuid import UUID

from fastapi import APIRouter, Response

from app.api.dependencies import CurrentAccount, Database
from app.api.v1.auth import PrivateValidationRoute
from app.schemas.consent import ConsentResponse, GrantConsent
from app.services.consents import change_consent, list_consents

router = APIRouter(
    prefix="/patients/me/consents", tags=["consent"], route_class=PrivateValidationRoute
)


@router.get("", response_model=list[ConsentResponse])
async def own_consents(
    db: Database, account: CurrentAccount, response: Response
) -> list[ConsentResponse]:
    result = await list_consents(db, account.id)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("", response_model=ConsentResponse)
async def grant(
    payload: GrantConsent, db: Database, account: CurrentAccount, response: Response
) -> ConsentResponse:
    result = await change_consent(db, account.id, payload.practitioner_id, grant=True)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/{practitioner_id}/revoke", response_model=ConsentResponse)
async def revoke(
    practitioner_id: UUID, db: Database, account: CurrentAccount, response: Response
) -> ConsentResponse:
    result = await change_consent(db, account.id, practitioner_id, grant=False)
    response.headers["Cache-Control"] = "no-store"
    return result
