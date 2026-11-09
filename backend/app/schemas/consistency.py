"""Internal consistency inputs/results; no public API contract or clinical verdict."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from app.schemas.clinical_fact import FactPayload, FactProvenance


class ConsistencySchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FactVersion(ConsistencySchema):
    version_id: UUID
    fact_id: UUID
    patient_id: UUID
    version: int
    previous_version_id: UUID | None = None
    previous_version: int | None = None
    attribution: Literal["patient_reported", "practitioner_managed"]
    author_id: UUID
    author_role: Literal["patient", "practitioner"]
    fact: FactPayload
    provenance: FactProvenance
    observed_at: AwareDatetime
    entered_at: AwareDatetime
    correction_reason: str | None = None
    entry_method: Literal["STRUCTURED", "LLM_EXTRACTED"] = "STRUCTURED"
    status: Literal["CONFIRMED", "PENDING", "REJECTED"] = "CONFIRMED"
    confirmed_by_id: UUID | None = None
    confirmed_at: AwareDatetime | None = None


class Finding(ConsistencySchema):
    rule_id: str
    category: Literal["INTEGRITY_ERROR", "POSSIBLE_DUPLICATE", "NOT_COMPARABLE"]
    reason_code: str
    fact_version_ids: tuple[UUID, ...]


class SkippedCheck(ConsistencySchema):
    rule_id: str
    reason_code: str


class Snapshot(ConsistencySchema):
    current_version_ids: tuple[UUID, ...]
    history_version_ids: tuple[UUID, ...]


class ConsistencyEvaluation(ConsistencySchema):
    patient_id: UUID
    ruleset_version: str
    snapshot: Snapshot
    findings: tuple[Finding, ...]
    checks_run: tuple[str, ...]
    checks_not_applicable: tuple[SkippedCheck, ...]


class ConsistencyRun(ConsistencySchema):
    evaluated_at: AwareDatetime
    evaluation: ConsistencyEvaluation
