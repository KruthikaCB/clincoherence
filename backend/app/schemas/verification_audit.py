"""Strict frozen metadata whitelist; no prose, values, credentials or release claims."""

from typing import Annotated, Literal, get_args
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, field_validator

from app.schemas.consistency import ConsistencySchema, Snapshot
from app.schemas.draft_verification import ClaimResult, Outcome
from app.schemas.evidence import Digest, EvidenceReference, Identifier, Status
from app.schemas.evidence_aware_draft import ClaimEvidenceBinding
from app.schemas.verification_gate import Reason

Code = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,99}$")]
CheckId = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{0,99}$")]
AuditDecision = Literal["PASS", "CLARIFY", "REFUSE"]


class StrictAuditSchema(ConsistencySchema):
    model_config = ConfigDict(strict=True)


class AuditSkipped(StrictAuditSchema):
    rule_id: CheckId
    reason_code: Code


class AuditChecks(StrictAuditSchema):
    checks_run: tuple[CheckId, ...]
    checks_not_applicable: tuple[AuditSkipped, ...]


class AuditGrounding(AuditChecks):
    outcome: Outcome
    reason_code: Code
    fact_version_ids: tuple[UUID, ...]

    @field_validator("reason_code")
    @classmethod
    def known_reason(cls, value: str) -> str:
        if value not in get_args(ClaimResult.model_fields["reason_code"].annotation):
            raise ValueError("Unknown grounding reason")
        return value


class AuditRule(AuditChecks):
    rule_id: Identifier
    rule_version: Identifier
    status: Status
    reason_code: Code
    evidence: tuple[EvidenceReference, ...]
    matched_fact_version_ids: tuple[UUID, ...]


class AuditEvidence(StrictAuditSchema):
    binding: ClaimEvidenceBinding
    approval_manifest_id: Identifier | None
    approval_manifest_version: Identifier | None
    approval_manifest_hash: Digest | None
    integrity: AuditRule
    applicability: AuditRule | None


class AuditClaim(AuditChecks):
    claim_id: UUID
    decision: AuditDecision
    reason_codes: tuple[Reason, ...]
    grounding: AuditGrounding | None
    evidence: AuditEvidence | None


class AuditFinding(StrictAuditSchema):
    rule_id: CheckId
    category: Literal["INTEGRITY_ERROR", "POSSIBLE_DUPLICATE", "NOT_COMPARABLE"]
    reason_code: Code
    fact_version_ids: tuple[UUID, ...]


class AuditVerification(StrictAuditSchema):
    record_digest: Digest
    composition_policy_version: Identifier
    grounding_policy_version: Identifier
    evidence_policy_version: Identifier
    renderer_version: Identifier
    consistency_ruleset_version: Identifier
    snapshot: Snapshot
    consistency_findings: tuple[AuditFinding, ...]
    catalogue_fingerprint: Digest | None


class AuditMetadata(AuditChecks):
    audit_schema_version: Literal["verification-audit-v1"] = "verification-audit-v1"
    event_kind: Literal["GATE_EVALUATED"] = "GATE_EVALUATED"
    patient_id: UUID
    actor_id: UUID
    actor_role: Literal["patient", "practitioner"]
    draft_id: UUID
    gate_policy_version: Identifier
    request_digest: Digest
    verification: AuditVerification | None
    claims: tuple[AuditClaim, ...] = Field(max_length=32)
    decision: AuditDecision
    reason_codes: tuple[Reason, ...]
    release_status: Literal["NOT_ASSERTED"] = "NOT_ASSERTED"
    http_release_authorized: Literal[False] = False
    unrestricted_text_verified: Literal[False] = False


class AuditEvent(StrictAuditSchema):
    id: UUID
    operation_id: UUID
    payload_hash: Digest
    metadata: AuditMetadata
    evaluated_at: AwareDatetime
    recorded_at: AwareDatetime
