"""Authenticated selection-only bridge to audited summary release."""

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audited_response import AuditedRecordResponse
from app.api.dependencies import CurrentAccount, patient_scope
from app.api.v1.auth import PrivateValidationRoute
from app.api.v1.verified_records import release_session_factory
from app.schemas.clinical_summary import ClinicalSummaryReleaseRequest
from app.schemas.consistency import ConsistencySchema


class SummaryRequest(ConsistencySchema):
    operation_id: UUID
    request: ClinicalSummaryReleaseRequest


router = APIRouter(route_class=PrivateValidationRoute)


@router.post(
    "/patients/{patient_id}/clinical-summary", response_model=None, tags=["clinical summary"]
)
async def clinical_summary(
    patient_id: UUID,
    payload: SummaryRequest,
    account: CurrentAccount,
    authorized_patient_id: Annotated[UUID, Depends(patient_scope)],
    factory: Annotated[Callable[[], AsyncSession], Depends(release_session_factory)],
) -> AuditedRecordResponse:
    # POST carries a fresh operation ID for audit; it never changes clinical facts.
    if patient_id != authorized_patient_id or payload.request.patient_id != patient_id:
        raise HTTPException(403, "Summary scope unavailable")
    return AuditedRecordResponse(
        account.id,
        patient_id,
        payload.request,
        payload.operation_id,
        factory,
        clinical_summary=True,
    )
