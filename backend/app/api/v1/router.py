"""
API v1 router aggregator.

Collects all v1 endpoint routers into a single router.
"""

from fastapi import APIRouter

from app.api.v1.auth import router as auth_router

api_v1_router = APIRouter()
api_v1_router.include_router(auth_router)

# Patient-record and conversation routers will be added in later milestones.
