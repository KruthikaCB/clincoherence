"""Authenticated bridge to the existing two-transaction audited ASGI adapter."""

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audited_response import AuditedRecordResponse
from app.api.dependencies import CurrentAccount, patient_scope
from app.api.v1.auth import PrivateValidationRoute
from app.core.database import async_session_factory
from app.schemas.verified_records import VerifiedRecordsRequest


def release_session_factory() -> Callable[[], AsyncSession]:
    """Trusted factory, independent of the authentication request's session."""
    return async_session_factory


router = APIRouter(route_class=PrivateValidationRoute)


@router.post(
    "/patients/{patient_id}/verified-records",
    response_model=None,
    tags=["verified records"],
    description=(
        "Audited accepted-record restatements only; not clinical correctness or delivery proof. "
        "Withheld requests return a generic error without clinical records."
    ),
)
async def verified_records(
    patient_id: UUID,
    payload: VerifiedRecordsRequest,
    account: CurrentAccount,
    authorized_patient_id: Annotated[UUID, Depends(patient_scope)],
    factory: Annotated[Callable[[], AsyncSession], Depends(release_session_factory)],
) -> AuditedRecordResponse:
    if payload.request.draft.patient_id != patient_id or authorized_patient_id != patient_id:
        raise HTTPException(409, "Verification scope mismatch")
    # Return the Response itself: never serialize/buffer its protected body here.
    # Initial scope approval is not a permit; 4H independently reauthorizes twice.
    return AuditedRecordResponse(
        account.id, patient_id, payload.request, payload.operation_id, factory
    )
