"""Minimal discovery identities, never clinical access credentials."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DiscoverySchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OwnProfile(DiscoverySchema):
    role: Literal["patient", "practitioner"]
    profile_id: UUID


class AccessiblePatient(DiscoverySchema):
    patient_id: UUID
