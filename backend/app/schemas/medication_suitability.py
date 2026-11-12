"""Educational label-check contract; intentionally no positive suitability status."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from app.schemas.consistency import FactVersion

Medication = Literal["metformin", "empagliflozin", "semaglutide"]
ReviewStatus = Literal["SOURCE_VERIFIED_CLINICAL_REVIEW_PENDING"]


class FrozenSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LabelSource(FrozenSchema):
    source_id: str
    organization: str
    title: str
    document_version: str
    published_on: date
    url: str
    stable_id: str
    sections: tuple[str, ...]


class LabelRule(FrozenSchema):
    rule_id: str
    medication: Medication
    source_id: str
    condition: Literal["egfr_lt_30", "egfr_30_through_45", "explicit_mtc_or_men2"]
    description: str
    scope: str


class LabelCatalog(FrozenSchema):
    catalog_version: Literal["capstone-label-checks-2026-11-12-v1"]
    reviewed_on: date
    review_status: ReviewStatus
    intended_use: Literal["SYNTHETIC_EDUCATIONAL_ONLY"]
    sources: tuple[LabelSource, ...]
    rules: tuple[LabelRule, ...]


class RuleCheck(FrozenSchema):
    rule: LabelRule
    outcome: Literal["TRIGGERED", "NOT_TRIGGERED", "UNKNOWN"]
    fact_version_ids: tuple[UUID, ...]


class MedicationAssessment(FrozenSchema):
    medication: Medication
    status: Literal["CAUTION_REVIEW_REQUIRED", "NOT_SUITABLE_UNDER_DOCUMENTED_RULE", "INSUFFICIENT_INFORMATION"]
    reasoning: tuple[str, ...]
    cautions: tuple[str, ...]
    missing_information: tuple[str, ...]
    fact_version_ids: tuple[UUID, ...]
    rule_checks: tuple[RuleCheck, ...]
    evidence: tuple[LabelSource, ...]
    clinician_review_required: Literal[True] = True


class MedicationSuitabilityResponse(FrozenSchema):
    representation_version: Literal["synthetic-medication-assessment-v1"] = "synthetic-medication-assessment-v1"
    patient_id: UUID
    mode: Literal["SYNTHETIC_EDUCATIONAL_ONLY"] = "SYNTHETIC_EDUCATIONAL_ONLY"
    confirmation_scope: Literal["SPEAKER_CONFIRMED_NOT_CLINICIAN_APPROVED"] = "SPEAKER_CONFIRMED_NOT_CLINICIAN_APPROVED"
    assessed_at: AwareDatetime
    catalog_version: str | None
    catalog_sha256: str | None
    evidence_review_status: Literal["SOURCE_VERIFIED_CLINICAL_REVIEW_PENDING", "UNAVAILABLE"]
    facts: tuple[FactVersion, ...]
    assessments: tuple[MedicationAssessment, ...]
    limitations: tuple[str, ...]
