"""Review metadata only: no clinical verdict, trusted actor or fact payload input."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

POLICY_VERSION = "clinician-fact-review-v1"
Decision = Literal["APPROVE", "REJECT", "WITHDRAW"]


class ReviewSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class CreateClinicalFactReview(ReviewSchema):
    operation_id: UUID
    expected_version_id: UUID
    # Required even for the first decision (explicit null).
    expected_review_id: UUID | None
    decision: Decision
    reason: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def reason_required(self) -> "CreateClinicalFactReview":
        if self.decision != "APPROVE" and self.reason is None:
            raise ValueError("Reject and withdraw require a reason")
        return self


class ClinicalFactReview(ReviewSchema):
    id: UUID
    patient_id: UUID
    fact_id: UUID
    fact_version_id: UUID
    reviewer_id: UUID
    operation_id: UUID
    sequence: int = Field(ge=1)
    previous_review_id: UUID | None
    decision: Decision
    reason: str | None
    policy_version: Literal["clinician-fact-review-v1"]
    recorded_at: AwareDatetime


class CurrentClinicalFactReview(ReviewSchema):
    patient_id: UUID
    fact_id: UUID
    fact_version_id: UUID
    reviewer_id: UUID
    review_status: Literal["UNREVIEWED", "APPROVED", "REJECTED", "WITHDRAWN"]
    latest_review: ClinicalFactReview | None
