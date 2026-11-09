"""Internal technical eligibility only; no clinical verdict or release permission."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, model_validator

from app.schemas.consistency import ConsistencySchema, Finding, SkippedCheck, Snapshot
from app.schemas.evidence import Digest
from app.schemas.evidence_aware_draft import (
    EvidenceAssessment,
    EvidenceAwareDraftRequest,
    EvidenceAwareRecord,
    GroundingAssessment,
)

Decision = Literal["PASS", "WARN", "CLARIFY", "REFUSE"]
Reason = Literal[
    "ACCEPTED_RECORD_RESTATEMENT",
    "SYNTHETIC_REFERENCE_CHECKS_COMPLETE",
    "FACT_RESTATEMENT_MISMATCH",
    "CLAIM_TYPE_UNSUPPORTED",
    "UNVERIFIED_PROSE",
    "UNRESTRICTED_PROSE_PRESENT",
    "ACCEPTED_REFERENCE_UNAVAILABLE",
    "ACCEPTED_STATE_INVALID",
    "REQUIRED_EVIDENCE_UNAVAILABLE",
    "EVIDENCE_REFERENCE_INVALID",
    "EVIDENCE_CONTEXT_NOT_APPLICABLE",
    "VERIFICATION_CONTRACT_INVALID",
    "CLAIM_COVERAGE_INVALID",
    "REQUIRED_INPUT_MISSING",
    "NO_SUPPORTED_CLAIMS_SELECTED",
    "RENDERER_TEXT_UNVERIFIED",
    "CATALOGUE_INTEGRITY_INVALID",
]


class GateInput(ConsistencySchema):
    # Trusted internal input only. Public/provider requests never contain a record.
    request: EvidenceAwareDraftRequest
    verification: EvidenceAwareRecord


class GateVerificationReference(ConsistencySchema):
    record_digest: Digest
    composition_policy_version: str
    grounding_policy_version: str
    evidence_policy_version: str
    renderer_version: str
    consistency_ruleset_version: str
    snapshot: Snapshot
    consistency_findings: tuple[Finding, ...]
    catalogue_fingerprint: Digest | None


class ClaimGateDecision(ConsistencySchema):
    claim_id: UUID
    decision: Decision
    reason_codes: tuple[Reason, ...]
    grounding: GroundingAssessment | None
    evidence: EvidenceAssessment | None
    checks_run: tuple[str, ...]
    checks_not_applicable: tuple[SkippedCheck, ...]

    @model_validator(mode="after")
    def no_warning_policy(self) -> "ClaimGateDecision":
        if self.decision == "WARN":
            raise ValueError("WARN is reserved")
        return self


class GatedRecordText(ConsistencySchema):
    claim_id: UUID
    text: str


class GateDecisionRecord(ConsistencySchema):
    draft_id: UUID
    patient_id: UUID
    policy_version: str
    request_digest: Digest
    verification: GateVerificationReference | None
    claims: tuple[ClaimGateDecision, ...]
    decision: Decision
    reason_codes: tuple[Reason, ...]
    rendered_records: tuple[GatedRecordText, ...]
    checks_run: tuple[str, ...]
    checks_not_applicable: tuple[SkippedCheck, ...]
    unrestricted_text_verified: Literal[False] = False
    http_release_authorized: Literal[False] = False

    @model_validator(mode="after")
    def batch_text_boundary(self) -> "GateDecisionRecord":
        if self.decision == "WARN":
            raise ValueError("WARN is reserved")
        if self.decision != "PASS" and self.rendered_records:
            raise ValueError("Withheld batches contain no text")
        if self.decision == "PASS" and (
            not self.claims
            or any(c.decision != "PASS" for c in self.claims)
            or len({c.claim_id for c in self.claims}) != len(self.claims)
            or tuple(c.claim_id for c in self.claims)
            != tuple(t.claim_id for t in self.rendered_records)
            or self.verification is None
        ):
            raise ValueError("PASS requires complete nonempty verified record coverage")
        return self


class GateRun(ConsistencySchema):
    evaluated_at: AwareDatetime
    gate: GateDecisionRecord
