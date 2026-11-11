"""Internal recorded-state summary; neither clinical advice nor release authority."""

from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime

from app.schemas.consistency import ConsistencySchema, FactVersion, Snapshot
from app.schemas.draft_verification import ClaimResult
from app.schemas.laboratory_comparison import LaboratoryComparison


class SummaryRecord(ConsistencySchema):
    source: FactVersion
    verification: ClaimResult


class SummaryObservation(ConsistencySchema):
    observed_at: AwareDatetime
    status: Literal["RECORDED", "AMBIGUOUS"]
    value: Decimal | None
    unit: str | None
    records: tuple[SummaryRecord, ...]


class SummaryChange(ConsistencySchema):
    direction: Literal["INCREASED", "DECREASED", "UNCHANGED", "UNAVAILABLE", "NOT_COMPARABLE"]
    reason_code: str
    comparison: LaboratoryComparison | None = None


class ClinicalSummary(ConsistencySchema):
    patient_id: UUID
    policy_version: str
    verification_policy_version: str
    renderer_version: str
    laboratory_policy_version: str
    snapshot: Snapshot
    # Newest observation first; only current accepted correction-chain leaves.
    hba1c_observations: tuple[SummaryObservation, ...]
    latest_hba1c: SummaryObservation | None
    previous_hba1c: SummaryObservation | None
    hba1c_change: SummaryChange
    latest_egfr: SummaryObservation | None
    medications: tuple[SummaryRecord, ...]
    missing_information: tuple[str, ...]
    text: str


class ClinicalSummaryRun(ConsistencySchema):
    evaluated_at: AwareDatetime
    summary: ClinicalSummary


class ClinicalSummaryReleaseRequest(ConsistencySchema):
    # Selection only: no client-authored summary, facts, PASS, digest or receipt.
    patient_id: UUID
    representation_version: Literal["clinical-summary-json-v1"] = "clinical-summary-json-v1"


class ClinicalSummaryResponse(ConsistencySchema):
    representation_version: Literal["clinical-summary-json-v1"] = "clinical-summary-json-v1"
    summary: ClinicalSummary
    verification_scope: Literal["RECORDED_STATE_ONLY_NOT_CLINICAL_CORRECTNESS"] = (
        "RECORDED_STATE_ONLY_NOT_CLINICAL_CORRECTNESS"
    )
