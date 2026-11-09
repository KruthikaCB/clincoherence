"""Internal immutable draft assertions and records, never accepted patient state."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from app.schemas.clinical_fact import (
    DiagnosisFact,
    HistoryFact,
    LaboratoryFact,
    MedicationFact,
    SymptomFact,
)
from app.schemas.consistency import ConsistencySchema, Finding, SkippedCheck, Snapshot


class FrozenDiagnosis(DiagnosisFact):
    model_config = ConfigDict(frozen=True)


class FrozenMedication(MedicationFact):
    model_config = ConfigDict(frozen=True)


class FrozenLaboratory(LaboratoryFact):
    model_config = ConfigDict(frozen=True)


class FrozenSymptom(SymptomFact):
    model_config = ConfigDict(frozen=True)


class FrozenHistory(HistoryFact):
    model_config = ConfigDict(frozen=True)


FrozenPayload = Annotated[
    FrozenDiagnosis | FrozenMedication | FrozenLaboratory | FrozenSymptom | FrozenHistory,
    Field(discriminator="kind"),
]
Outcome = Literal["GROUNDED", "UNGROUNDED", "UNSUPPORTED", "UNVERIFIABLE", "INVALID"]


class SourceAttribution(ConsistencySchema):
    author_id: UUID
    author_role: Literal["patient", "practitioner"]
    category: Literal["patient_reported", "practitioner_managed"]


class RestatementContent(ConsistencySchema):
    fact: FrozenPayload
    observed_at: AwareDatetime


class DraftClaim(ConsistencySchema):
    claim_id: UUID
    patient_id: UUID
    claim_type: str = Field(min_length=1, max_length=80)
    fact_version_ids: tuple[UUID, ...] = Field(max_length=8)
    source_attribution: SourceAttribution | None = None
    proposed_content: RestatementContent | Annotated[str, Field(max_length=4000)] | None = None


class DraftVerificationRequest(ConsistencySchema):
    draft_id: UUID
    patient_id: UUID
    claims: tuple[DraftClaim, ...] = Field(max_length=32)
    # Never interpreted, rendered, echoed or marked verified.
    unverified_text: str | None = Field(default=None, max_length=16000)

    @model_validator(mode="after")
    def unique_claim_ids(self) -> "DraftVerificationRequest":
        if len({c.claim_id for c in self.claims}) != len(self.claims):
            raise ValueError("Repeated claim identity")
        return self


class ClaimResult(ConsistencySchema):
    claim_id: UUID
    outcome: Outcome
    reason_code: Literal[
        "FACT_RESTATEMENT_MATCH",
        "REFERENCED_STATE_UNAVAILABLE",
        "CLAIM_TYPE_UNSUPPORTED",
        "ONE_FACT_REFERENCE_REQUIRED",
        "STRUCTURED_CONTENT_REQUIRED",
        "SOURCE_ATTRIBUTION_REQUIRED",
        "ACCEPTED_STATE_INVALID",
        "FACT_KIND_MISMATCH",
        "FACT_CODE_MISMATCH",
        "FACT_UNIT_MISMATCH",
        "FACT_VALUE_MISMATCH",
        "FACT_CONTENT_MISMATCH",
        "OBSERVATION_TIME_MISMATCH",
        "SOURCE_ATTRIBUTION_MISMATCH",
    ]
    fact_version_ids: tuple[UUID, ...]
    rendered_text: str | None
    text_verified: bool
    checks_run: tuple[str, ...]
    checks_not_applicable: tuple[SkippedCheck, ...]

    @model_validator(mode="after")
    def renderer_boundary(self) -> "ClaimResult":
        grounded = self.outcome == "GROUNDED"
        if self.text_verified != grounded or (self.rendered_text is not None) != grounded:
            raise ValueError("Only grounded results may include verified renderer text")
        return self


class DraftVerificationRecord(ConsistencySchema):
    draft_id: UUID
    patient_id: UUID
    policy_version: str
    renderer_version: str
    snapshot: Snapshot
    consistency_ruleset_version: str
    consistency_findings: tuple[Finding, ...]
    claims: tuple[ClaimResult, ...]
    unrestricted_text_verified: Literal[False] = False


class DraftVerificationRun(ConsistencySchema):
    evaluated_at: AwareDatetime
    verification: DraftVerificationRecord
