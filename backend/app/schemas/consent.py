"""Consent requests contain no caller-selected patient or actor identity."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class GrantConsent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Practitioner profile UUID, not account UUID. Prototype eligibility applies.
    practitioner_id: UUID


class ConsentResponse(BaseModel):
    consent_id: UUID
    practitioner_id: UUID
    status: Literal["granted", "revoked"]
    granted_at: datetime
    revoked_at: datetime | None
