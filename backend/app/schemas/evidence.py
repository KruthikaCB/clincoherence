"""Synthetic-only evidence contracts; no clinical verdict or caller approval flag."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.schemas.consistency import ConsistencySchema, Finding, SkippedCheck, Snapshot

Identifier = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,99}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Status = Literal["RESOLVED", "UNAVAILABLE", "NOT_APPLICABLE", "INSUFFICIENT_CONTEXT", "ERROR"]
Label = Literal["SYNTHETIC_FIXTURE_ONLY"]


class EvidenceSource(ConsistencySchema):
    source_id: Identifier
    version: Identifier
    edition: Identifier
    label: Label
    title: str
    publisher: Literal["ClinCoherence synthetic fixtures"]
    canonical_location: str = Field(pattern=r"^fixture://[a-z0-9/_.-]+$")
    locators: tuple[str, ...]
    content_hash: Digest


class EvidenceReference(ConsistencySchema):
    source_id: Identifier
    source_version: Identifier
    source_edition: Identifier
    source_content_hash: Digest
    unit_id: Identifier
    unit_version: Identifier
    unit_content_hash: Digest


class EvidenceUnit(ConsistencySchema):
    source_id: Identifier
    source_version: Identifier
    source_content_hash: Digest
    unit_id: Identifier
    version: Identifier
    label: Label
    locator: str = Field(pattern=r"^section:[a-z0-9-]+$")
    content: str = Field(min_length=1, max_length=4000)
    content_hash: Digest


class StructuredQualifier(ConsistencySchema):
    field: Literal["attribution", "author_role", "entry_method"]
    equals: str

    @model_validator(mode="after")
    def supported_value(self) -> "StructuredQualifier":
        allowed = {
            "attribution": {"patient_reported", "practitioner_managed"},
            "author_role": {"patient", "practitioner"},
            "entry_method": {"STRUCTURED", "LLM_EXTRACTED"},
        }
        if self.equals not in allowed[self.field]:
            raise ValueError("Unsupported structured qualifier")
        return self


class FactRequirement(ConsistencySchema):
    kind: Literal["laboratory"]
    code: Literal["hba1c", "glucose", "creatinine", "egfr", "urine_albumin_creatinine_ratio"]
    unit: str
    qualifiers: tuple[StructuredQualifier, ...] = ()


class RuleDefinition(ConsistencySchema):
    rule_id: Identifier
    version: Identifier
    label: Label
    domain: Literal["T2DM"]
    intended_uses: tuple[Identifier, ...]
    logic_version: Literal["synthetic-structured-context-v1"]
    evidence: tuple[EvidenceReference, ...] = Field(min_length=1)
    requirements: tuple[FactRequirement, ...] = Field(min_length=1)
    content_hash: Digest


class EvidenceApproval(ConsistencySchema):
    evidence: EvidenceReference
    rule_id: Identifier
    rule_version: Identifier
    rule_content_hash: Digest
    intended_use: Identifier
    status: Literal["approved", "withdrawn"]
    approval_reference: Literal["milestone-4c-synthetic-only"]


class ApprovalManifest(ConsistencySchema):
    manifest_id: Identifier
    version: Identifier
    label: Label
    approvals: tuple[EvidenceApproval, ...]


class EvidenceBundle(ConsistencySchema):
    label: Label
    sources: tuple[EvidenceSource, ...]
    units: tuple[EvidenceUnit, ...]


class RuleRegistry(ConsistencySchema):
    label: Label
    rules: tuple[RuleDefinition, ...]


class RuleSelection(ConsistencySchema):
    rule_id: Identifier
    rule_version: Identifier
    evidence: tuple[EvidenceReference, ...]


class VerificationRequest(ConsistencySchema):
    patient_id: UUID
    fact_version_ids: tuple[UUID, ...]
    rules: tuple[RuleSelection, ...]
    approval_manifest_id: Identifier
    approval_manifest_version: Identifier
    intended_use: Identifier

    @model_validator(mode="after")
    def unique_rules(self) -> "VerificationRequest":
        keys = [(r.rule_id, r.rule_version) for r in self.rules]
        if len(set(keys)) != len(keys):
            raise ValueError("Repeated rule selection")
        return self


class RuleEvaluation(ConsistencySchema):
    rule_id: Identifier
    rule_version: Identifier
    status: Status
    reason_code: str
    evidence: tuple[EvidenceReference, ...] = ()
    matched_fact_version_ids: tuple[UUID, ...] = ()
    checks_run: tuple[str, ...]
    checks_not_applicable: tuple[SkippedCheck, ...]


class EvidenceEvaluation(ConsistencySchema):
    patient_id: UUID
    policy_version: str
    snapshot: Snapshot
    consistency_ruleset_version: str
    consistency_findings: tuple[Finding, ...]
    approval_manifest_id: Identifier | None
    approval_manifest_version: Identifier | None
    approval_manifest_hash: Digest | None
    status: Status
    reason_code: str
    rules: tuple[RuleEvaluation, ...]


class EvidenceRun(ConsistencySchema):
    evaluated_at: AwareDatetime
    evaluation: EvidenceEvaluation
