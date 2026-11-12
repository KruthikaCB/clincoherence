"""Separate clinician review APIs; no confirmation or recommendation semantics."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.dependencies import CurrentAccount, Database
from app.api.v1.auth import PrivateValidationRoute
from app.api.v1.clinical_facts import ClinicalResponse
from app.schemas.clinical_fact_review import (
    ClinicalFactReview,
    CreateClinicalFactReview,
    CurrentClinicalFactReview,
)
from app.services.clinical_fact_review import create_review, current_reviews, review_history

router = APIRouter(
    prefix="/patients/{patient_id}", tags=["clinician fact review"],
    route_class=PrivateValidationRoute,
)
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100000)]


@router.get("/clinical-fact-reviews", response_model=list[CurrentClinicalFactReview])
async def current(
    patient_id: UUID, db: Database, account: CurrentAccount, limit: Limit = 100, offset: Offset = 0,
) -> ClinicalResponse:
    result = await current_reviews(db, account.id, patient_id, limit=limit, offset=offset)
    return ClinicalResponse(db, account.id, patient_id, result)


@router.get("/facts/{fact_id}/clinical-reviews", response_model=list[ClinicalFactReview])
async def history(
    patient_id: UUID, fact_id: UUID, db: Database, account: CurrentAccount,
    limit: Limit = 100, offset: Offset = 0,
) -> ClinicalResponse:
    result = await review_history(db, account.id, patient_id, fact_id, limit=limit, offset=offset)
    return ClinicalResponse(db, account.id, patient_id, result)


@router.post("/facts/{fact_id}/clinical-reviews", response_model=ClinicalFactReview)
async def decide(
    patient_id: UUID, fact_id: UUID, payload: CreateClinicalFactReview,
    db: Database, account: CurrentAccount,
) -> ClinicalResponse:
    result = await create_review(db, account.id, patient_id, fact_id, payload)
    return ClinicalResponse(db, account.id, patient_id, result)
