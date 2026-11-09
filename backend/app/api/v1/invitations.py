"""Body-only secret submission; invitation URLs use fragments to avoid access logs."""

from uuid import UUID

from fastapi import APIRouter, Response

from app.api.dependencies import CurrentAccount, Database
from app.api.v1.auth import PrivateValidationRoute
from app.schemas.invitation import (
    ActivatePatient,
    CreateInvitation,
    InvitationCreated,
    InvitationInspection,
    InvitationSecret,
    InvitationState,
    PatientActivated,
)
from app.services.invitations import (
    activate_patient,
    create_invitation,
    managed_invitation,
    valid_invitation,
)

router = APIRouter(
    prefix="/patient-invitations", tags=["onboarding"], route_class=PrivateValidationRoute
)


def private_response(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


@router.post("", response_model=InvitationCreated, status_code=201)
async def create(
    payload: CreateInvitation, db: Database, account: CurrentAccount, response: Response
) -> InvitationCreated:
    result = await create_invitation(db, account.id, payload.intended_identity)
    private_response(response)
    return result


@router.post("/inspect", response_model=InvitationInspection)
async def inspect(
    payload: InvitationSecret, db: Database, response: Response
) -> InvitationInspection:
    invitation = await valid_invitation(db, payload.token.get_secret_value())
    private_response(response)
    return InvitationInspection(expires_at=invitation.expires_at)


@router.post("/activate", response_model=PatientActivated, status_code=201)
async def activate(payload: ActivatePatient, db: Database, response: Response) -> PatientActivated:
    result = await activate_patient(db, payload)
    private_response(response)
    return result


@router.get("/{invitation_id}", response_model=InvitationState)
async def state(
    invitation_id: UUID, db: Database, account: CurrentAccount, response: Response
) -> InvitationState:
    result = await managed_invitation(db, account.id, invitation_id)
    private_response(response)
    return result


@router.post("/{invitation_id}/revoke", response_model=InvitationState)
async def revoke(
    invitation_id: UUID, db: Database, account: CurrentAccount, response: Response
) -> InvitationState:
    result = await managed_invitation(db, account.id, invitation_id, revoke=True)
    private_response(response)
    return result
