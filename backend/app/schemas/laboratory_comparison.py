"""Internal descriptive laboratory comparison contracts; no clinical verdicts."""

from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from app.schemas.consistency import ConsistencyEvaluation, ConsistencySchema, SkippedCheck


class VersionPair(ConsistencySchema):
    first_version_id: UUID
    second_version_id: UUID


class LaboratoryComparison(ConsistencySchema):
    rule_id: Literal["LC-LAB-DIFFERENCE-01"] = "LC-LAB-DIFFERENCE-01"
    earlier_version_id: UUID
    later_version_id: UUID
    numeric_difference: Decimal = Field(allow_inf_nan=False)
    unit: str


class NotComparable(ConsistencySchema):
    rule_id: Literal["LC-LAB-ELIGIBILITY-01"] = "LC-LAB-ELIGIBILITY-01"
    category: Literal["NOT_COMPARABLE"] = "NOT_COMPARABLE"
    reason_code: Literal[
        "LABORATORY_FACTS_REQUIRED",
        "DISTINCT_FACT_ROOTS_REQUIRED",
        "LABORATORY_CODE_MISMATCH",
        "UNIT_MISMATCH_NO_CONVERSION",
        "AUTHOR_MISMATCH",
        "ATTRIBUTION_MISMATCH",
        "DISTINCT_OBSERVATION_TIMES_REQUIRED",
    ]
    fact_version_ids: tuple[UUID, UUID]


class LaboratoryEvaluation(ConsistencySchema):
    patient_id: UUID
    policy_version: str
    consistency: ConsistencyEvaluation
    requested_pairs: tuple[VersionPair, ...]
    comparisons: tuple[LaboratoryComparison, ...]
    not_comparable: tuple[NotComparable, ...]
    checks_run: tuple[str, ...]
    checks_not_applicable: tuple[SkippedCheck, ...]


class LaboratoryRun(ConsistencySchema):
    evaluated_at: AwareDatetime
    evaluation: LaboratoryEvaluation
