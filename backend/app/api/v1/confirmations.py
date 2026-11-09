"""Individual decisions only; pending source content remains speaker-private."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends

from app.api.dependencies import CurrentAccount, Database
from app.api.v1.auth import PrivateValidationRoute
from app.api.v1.clinical_facts import ClinicalResponse
from app.schemas.extraction import (
    ConfirmationResponse,
    ExtractionResponse,
    ExtractMessage,
    IndividualDecision,
)
from app.services.confirmations import decide, extract_message, own_confirmations
from app.services.extraction_provider import ExtractionProvider, extraction_provider

router = APIRouter(
    prefix="/patients/{patient_id}",
    tags=["extraction and confirmation"],
    route_class=PrivateValidationRoute,
)
Provider = Annotated[ExtractionProvider, Depends(extraction_provider)]


@router.post("/extractions", response_model=ExtractionResponse, status_code=201)
async def extract(
    patient_id: UUID,
    payload: ExtractMessage,
    db: Database,
    account: CurrentAccount,
    provider: Provider,
) -> ClinicalResponse:
    result = await extract_message(db, account.id, patient_id, payload, provider)
    return ClinicalResponse(db, account.id, patient_id, result, status_code=201)


@router.get("/confirmations", response_model=list[ConfirmationResponse])
async def confirmations(
    patient_id: UUID, db: Database, account: CurrentAccount
) -> ClinicalResponse:
    result = await own_confirmations(db, account.id, patient_id)
    return ClinicalResponse(db, account.id, patient_id, result)


@router.post("/confirmations/{confirmation_id}/confirm", response_model=ConfirmationResponse)
async def confirm(
    patient_id: UUID,
    confirmation_id: UUID,
    db: Database,
    account: CurrentAccount,
    payload: Annotated[IndividualDecision | None, Body()] = None,
) -> ClinicalResponse:
    result = await decide(db, account.id, patient_id, confirmation_id, confirm=True)
    return ClinicalResponse(db, account.id, patient_id, result)


@router.post("/confirmations/{confirmation_id}/reject", response_model=ConfirmationResponse)
async def reject(
    patient_id: UUID,
    confirmation_id: UUID,
    db: Database,
    account: CurrentAccount,
    payload: Annotated[IndividualDecision | None, Body()] = None,
) -> ClinicalResponse:
    result = await decide(db, account.id, patient_id, confirmation_id, confirm=False)
    return ClinicalResponse(db, account.id, patient_id, result)
