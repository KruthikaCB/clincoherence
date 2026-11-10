"""Authenticated deterministic questions, composed with the unchanged 4H adapter."""

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.api.audited_response import AuditedRecordResponse
from app.api.dependencies import CurrentAccount, Database, patient_scope
from app.api.v1.auth import PrivateValidationRoute
from app.api.v1.clinical_facts import ClinicalResponse
from app.api.v1.verified_records import release_session_factory
from app.schemas.record_questions import QuestionStatus, RecordQuestion
from app.services.record_questions import prepare_question

router = APIRouter(route_class=PrivateValidationRoute)


@router.post(
    "/patients/{patient_id}/record-questions", response_model=None, tags=["record questions"]
)
async def record_question(
    patient_id: UUID,
    payload: RecordQuestion,
    account: CurrentAccount,
    db: Database,
    authorized_patient_id: Annotated[UUID, Depends(patient_scope)],
    factory: Annotated[Callable[[], AsyncSession], Depends(release_session_factory)],
) -> Response:
    if authorized_patient_id != patient_id:
        raise HTTPException(403, "Patient scope denied")
    # T0: authorized selection only. No clinical body is sent or cached here.
    async with factory() as lookup:
        try:
            result = await prepare_question(lookup, account.id, patient_id, payload)
        finally:
            await lookup.rollback()
    if isinstance(result, QuestionStatus):
        # Fixed nonclinical protocol status, not a clinical gate or record-absence assertion.
        # Even selection-dependent status metadata retains fresh scope locks through handoff.
        return ClinicalResponse(db, account.id, patient_id, result.model_dump())
    # No verified text, PASS, receipt or bytes supplied: adapter independently checks T1/T2.
    return AuditedRecordResponse(
        account.id,
        patient_id,
        result,
        payload.operation_id,
        factory,
        include_provenance=payload.include_synthetic_provenance,
    )
