"""Prototype-only request/response allowlists. Never expose stored digests."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

PROTOTYPE_NOTICE = (
    "Prototype-only synthetic-user identity verification; not production-grade. "
    "Deliver the verification code separately from the invitation link. "
    "Activation does not grant practitioner consent."
)


class CreateInvitation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intended_identity: str = Field(pattern=r"^[a-z0-9][a-z0-9._+-]{0,63}@synthetic\.invalid$")

    @field_validator("intended_identity", mode="before")
    @classmethod
    def normalize_identity(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class InvitationSecret(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr


class ActivatePatient(InvitationSecret):
    identity: str
    verification_code: SecretStr
    password: SecretStr


class InvitationCreated(BaseModel):
    invitation_id: UUID
    patient_id: UUID
    invitation_url: str
    prototype_verification_code: str
    expires_at: datetime
    prototype_notice: str = PROTOTYPE_NOTICE


class InvitationInspection(BaseModel):
    valid: Literal[True] = True
    expires_at: datetime
    prototype_notice: str = PROTOTYPE_NOTICE


class InvitationState(BaseModel):
    invitation_id: UUID
    patient_id: UUID
    status: Literal["pending", "consumed", "revoked"]
    expires_at: datetime


class PatientActivated(BaseModel):
    account_id: UUID
    patient_id: UUID
    prototype_notice: str = PROTOTYPE_NOTICE
