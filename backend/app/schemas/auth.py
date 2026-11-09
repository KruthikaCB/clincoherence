"""Explicit response allowlists; never serialize ORM password hashes."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr


class LoginRequest(BaseModel):
    identity: str = Field(min_length=1, max_length=320)
    password: SecretStr


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class AccountResponse(BaseModel):
    id: UUID
    role: Literal["patient", "practitioner"]


class PatientScopeResponse(BaseModel):
    patient_id: UUID
