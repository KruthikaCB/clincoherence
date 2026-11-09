"""Internal synthetic evidence composition, not a clinical correctness contract."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.schemas.consistency import ConsistencySchema, Finding, SkippedCheck, Snapshot
from app.schemas.draft_verification import DraftVerificationRequest, Outcome
from app.schemas.evidence import Digest, Identifier, RuleEvaluation, RuleSelection


class ClaimEvidenceBinding(ConsistencySchema):
    claim_id: UUID
    rule: RuleSelection
    approval_manifest_id: Identifier
    approval_manifest_version: Identifier
    intended_use: Identifier


class EvidenceAwareDraftRequest(ConsistencySchema):
    draft: DraftVerificationRequest
    bindings: tuple[ClaimEvidenceBinding, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def known_unique_bindings(self) -> "EvidenceAwareDraftRequest":
        ids = [b.claim_id for b in self.bindings]
        if len(set(ids)) != len(ids) or not set(ids) <= {c.claim_id for c in self.draft.claims}:
            raise ValueError("Bindings require unique existing claims")
        return self


class GroundingAssessment(ConsistencySchema):
    outcome: Outcome
    # Copied only from the existing 4D Literal reason-code contract.
    reason_code: str
    fact_version_ids: tuple[UUID, ...]
    checks_run: tuple[str, ...]
    checks_not_applicable: tuple[SkippedCheck, ...]


class EvidenceAssessment(ConsistencySchema):
    binding: ClaimEvidenceBinding
    approval_manifest_id: Identifier | None
    approval_manifest_version: Identifier | None
    approval_manifest_hash: Digest | None
    integrity: RuleEvaluation
    applicability: RuleEvaluation | None


class EvidenceAwareClaimResult(ConsistencySchema):
    claim_id: UUID
    grounding: GroundingAssessment
    evidence: EvidenceAssessment | None
    reason_code: Literal[
        "EVIDENCE_NOT_REQUESTED",
        "CLAIM_NOT_GROUNDED",
        "SYNTHETIC_CONTEXT_MATCHED",
        "EVIDENCE_WITHHELD",
        "CATALOG_UNAVAILABLE_OR_INVALID",
        "ACCEPTED_STATE_INVALID",
    ]
    rendered_text: str | None
    text_verified: bool
    checks_run: tuple[str, ...]
    checks_not_applicable: tuple[SkippedCheck, ...]

    @model_validator(mode="after")
    def text_boundary(self) -> "EvidenceAwareClaimResult":
        eligible = self.grounding.outcome == "GROUNDED" and (
            self.evidence is None
            or (
                self.evidence.integrity.status == "RESOLVED"
                and self.evidence.applicability is not None
                and self.evidence.applicability.status == "RESOLVED"
            )
        )
        if self.text_verified != (self.rendered_text is not None):
            raise ValueError("Text verification requires renderer text")
        if self.text_verified and not eligible:
            raise ValueError("Text requires grounding and requested evidence checks")
        return self


class EvidenceAwareRecord(ConsistencySchema):
    draft_id: UUID
    patient_id: UUID
    policy_version: str
    grounding_policy_version: str
    evidence_policy_version: str
    renderer_version: str
    catalogue_fingerprint: Digest | None
    snapshot: Snapshot
    consistency_ruleset_version: str
    consistency_findings: tuple[Finding, ...]
    claims: tuple[EvidenceAwareClaimResult, ...]
    unrestricted_text_verified: Literal[False] = False


class EvidenceAwareRun(ConsistencySchema):
    evaluated_at: AwareDatetime
    verification: EvidenceAwareRecord
