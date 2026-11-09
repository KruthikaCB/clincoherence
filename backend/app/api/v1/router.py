"""
API v1 router aggregator.

Collects all v1 endpoint routers into a single router.
"""

from fastapi import APIRouter

api_v1_router = APIRouter()

# Future routers will be included here:
# from app.api.v1.endpoints import auth, patients, conversations
# api_v1_router.include_router(auth.router, prefix="/auth", tags=["auth"])
# api_v1_router.include_router(patients.router, prefix="/patients", tags=["patients"])
# api_v1_router.include_router(conversations.router, prefix="/conversations", tags=["conversations"])

