"""
API v1 router aggregator.

Collects all v1 endpoint routers into a single router.
"""

from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.clinical_facts import router as clinical_fact_router
from app.api.v1.confirmations import router as confirmation_router
from app.api.v1.consents import router as consent_router
from app.api.v1.invitations import router as invitations_router
from app.api.v1.patient_discovery import router as patient_discovery_router
from app.api.v1.verified_records import router as verified_records_router

api_v1_router = APIRouter()
api_v1_router.include_router(auth_router)
api_v1_router.include_router(clinical_fact_router)
api_v1_router.include_router(confirmation_router)
api_v1_router.include_router(consent_router)
api_v1_router.include_router(invitations_router)
api_v1_router.include_router(patient_discovery_router)
api_v1_router.include_router(verified_records_router)

# Patient-record and conversation routers will be added in later milestones.
