"""Authorized, read-only educational label checks; not an audited clinical release."""

from uuid import UUID

from fastapi import APIRouter

from app.api.dependencies import CurrentAccount, Database
from app.api.v1.auth import PrivateValidationRoute
from app.api.v1.clinical_facts import ClinicalResponse
from app.schemas.medication_suitability import MedicationSuitabilityResponse
from app.services.medication_suitability import patient_assessment

router = APIRouter(route_class=PrivateValidationRoute)


@router.get("/patients/{patient_id}/medication-suitability", response_model=MedicationSuitabilityResponse)
async def medication_suitability(patient_id: UUID, db: Database, account: CurrentAccount):
    result = await patient_assessment(db, account.id, patient_id)
    return ClinicalResponse(db, account.id, patient_id, result)
