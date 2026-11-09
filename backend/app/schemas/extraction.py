"""Untrusted provider output and speaker-scoped confirmation response schemas."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt

from app.schemas.clinical_fact import FactPayload


class StrictExtractionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractMessage(StrictExtractionSchema):
    source_message_id: UUID
    text: str = Field(min_length=1, max_length=16000)
    data_origin: Literal["synthetic", "de_identified"]


class ProviderMetadata(StrictExtractionSchema):
    name: str = Field(pattern=r"^[a-z0-9_.-]{1,80}$")
    version: str = Field(pattern=r"^[a-z0-9_.-]{1,80}$")


class ExtractedCandidate(StrictExtractionSchema):
    fact: FactPayload
    observed_at: AwareDatetime
    source_start: Annotated[StrictInt, Field(ge=0)]
    source_end: Annotated[StrictInt, Field(gt=0)]
    source_quote: str = Field(min_length=1, max_length=2000)


class IndividualDecision(StrictExtractionSchema):
    """An empty body may be supplied; no edits, identities or bulk decisions."""


class ConfirmationResponse(BaseModel):
    confirmation_id: UUID
    patient_id: UUID
    source_message_id: UUID
    speaker_id: UUID
    speaker_role: Literal["patient", "practitioner"]
    attribution: Literal["patient_reported", "practitioner_managed"]
    proposed_fact: FactPayload
    observed_at: datetime
    source_start: int
    source_end: int
    source_quote: str
    provider: ProviderMetadata
    grounding_version: str
    source_sha256: str
    data_origin: Literal["synthetic", "de_identified"]
    created_at: datetime
    status: Literal["PENDING", "CONFIRMED", "REJECTED"]
    decided_at: datetime | None
    accepted_version_id: UUID | None


class ExtractionResponse(BaseModel):
    source_message_id: UUID
    confirmations: list[ConfirmationResponse]
