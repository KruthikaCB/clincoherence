"""Structural T2DM prototype vocabulary; no clinical thresholds or recommendations."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class StrictFactSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


Text = Annotated[str, Field(min_length=1, max_length=2000)]
Name = Annotated[str, Field(min_length=1, max_length=200)]
Number = Annotated[Decimal, Field(allow_inf_nan=False, max_digits=24, decimal_places=10)]


class DiagnosisFact(StrictFactSchema):
    kind: Literal["diagnosis"]
    code: Literal["type_2_diabetes"]
    description: Text | None = None


class MedicationFact(StrictFactSchema):
    kind: Literal["medication"]
    medication_name: Name
    dose_value: Number | None = None
    dose_unit: Name | None = None
    frequency: Name | None = None

    @model_validator(mode="after")
    def dose_pair(self) -> "MedicationFact":
        if (self.dose_value is None) != (self.dose_unit is None):
            raise ValueError("A recorded dose needs both value and unit")
        return self


class LaboratoryFact(StrictFactSchema):
    kind: Literal["laboratory"]
    code: Literal["hba1c", "glucose", "creatinine", "egfr", "urine_albumin_creatinine_ratio"]
    value: Number
    unit: Name

    @model_validator(mode="after")
    def compatible_unit(self) -> "LaboratoryFact":
        units = {
            "hba1c": {"%", "mmol/mol"},
            "glucose": {"mg/dL", "mmol/L"},
            "creatinine": {"mg/dL", "umol/L"},
            "egfr": {"mL/min/1.73m2"},
            "urine_albumin_creatinine_ratio": {"mg/g", "mg/mmol"},
        }
        if self.unit not in units[self.code]:
            raise ValueError("Unsupported unit for this prototype laboratory code")
        return self


class SymptomFact(StrictFactSchema):
    kind: Literal["symptom"]
    code: Literal["polyuria", "polydipsia", "fatigue", "other"]
    description: Text


class HistoryFact(StrictFactSchema):
    kind: Literal["history"]
    category: Literal["family_history", "allergy", "smoking", "prior_event", "other"]
    description: Text


FactPayload = Annotated[
    DiagnosisFact | MedicationFact | LaboratoryFact | SymptomFact | HistoryFact,
    Field(discriminator="kind"),
]


class FactProvenance(StrictFactSchema):
    source: Name
    reference: Text | None = None
    # Caller declaration, not proof of de-identification or clinical authority.
    data_origin: Literal["synthetic", "de_identified"]


class CreateFact(StrictFactSchema):
    fact: FactPayload
    observed_at: AwareDatetime
    provenance: FactProvenance


class CorrectFact(CreateFact):
    expected_version_id: UUID
    correction_reason: Text


class FactResponse(BaseModel):
    fact_id: UUID
    version_id: UUID
    version: int
    previous_version_id: UUID | None
    patient_id: UUID
    attribution: Literal["patient_reported", "practitioner_managed"]
    author_id: UUID
    author_role: Literal["patient", "practitioner"]
    fact: FactPayload
    observed_at: datetime
    entered_at: datetime
    provenance: FactProvenance
    entry_method: Literal["STRUCTURED", "LLM_EXTRACTED"]
    status: Literal["CONFIRMED"]
    correction_reason: str | None
    confirmed_by_id: UUID | None
    confirmed_at: datetime | None
