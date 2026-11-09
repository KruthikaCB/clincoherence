"""Pure composition of trusted 4C resolution and independent 4D verification."""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from app.schemas.consistency import FactVersion, SkippedCheck
from app.schemas.evidence import VerificationRequest
from app.schemas.evidence_aware_draft import (
    ClaimEvidenceBinding,
    EvidenceAssessment,
    EvidenceAwareClaimResult,
    EvidenceAwareDraftRequest,
    EvidenceAwareRecord,
    GroundingAssessment,
)
from app.services.draft_verifier import verify_fact_claims
from app.services.evidence_catalog import (
    PreparedEvidence,
    canonical_hash,
    load_catalog,
    prepare_evidence,
)
from app.services.evidence_evaluator import POLICY_VERSION as EVIDENCE_POLICY
from app.services.evidence_evaluator import evaluate_evidence_context

POLICY_VERSION = "synthetic-evidence-aware-restatement-v1"
COMPOSITION = "EA-COMPOSITION-01"


@dataclass(frozen=True)
class PreparedBatch:
    fingerprint: str | None
    evidence: tuple[tuple[UUID, PreparedEvidence], ...]


def evidence_request(
    request: EvidenceAwareDraftRequest, binding: ClaimEvidenceBinding
) -> VerificationRequest:
    claim = next(c for c in request.draft.claims if c.claim_id == binding.claim_id)
    return VerificationRequest(
        patient_id=request.draft.patient_id,
        fact_version_ids=claim.fact_version_ids,
        rules=(binding.rule,),
        approval_manifest_id=binding.approval_manifest_id,
        approval_manifest_version=binding.approval_manifest_version,
        intended_use=binding.intended_use,
    )


def catalogue_fingerprint() -> str | None:
    try:
        catalog = load_catalog()
        return canonical_hash(
            {
                "bundle": catalog.bundle.model_dump(mode="json"),
                "registry": catalog.registry.model_dump(mode="json"),
                "manifest": catalog.manifest.model_dump(mode="json"),
            }
        )
    except (OSError, ValueError):
        return None


def prepare_batch(request: EvidenceAwareDraftRequest) -> PreparedBatch:
    """Trusted resolver output only; never accept PreparedBatch from a provider."""
    before = catalogue_fingerprint()
    items = tuple(
        (b.claim_id, prepare_evidence(evidence_request(request, b)))
        for b in sorted(request.bindings, key=lambda b: str(b.claim_id))
    )
    after = catalogue_fingerprint()
    valid = (
        before is not None
        and before == after
        and all(p.manifest_hash is not None for _, p in items)
    )
    return PreparedBatch(before if valid else None, items)


def evaluate_evidence_aware_draft(
    patient_id: UUID,
    current: Sequence[FactVersion],
    history: Sequence[FactVersion],
    request: EvidenceAwareDraftRequest,
    prepared: PreparedBatch,
) -> EvidenceAwareRecord:
    """No trust in caller assertions; prepared material is internal trusted input.

    Existing 4D preflight suppresses all text for unavailable versions. Existing
    4A validation and 4C applicability rules are reused, never reimplemented.
    """
    grounding = verify_fact_claims(patient_id, current, history, request.draft)
    bindings = {b.claim_id: b for b in request.bindings}
    resolutions = dict(prepared.evidence)
    invalid_state = any(f.category == "INTEGRITY_ERROR" for f in grounding.consistency_findings)
    output = []
    for result in grounding.claims:
        summary = GroundingAssessment.model_validate(
            result.model_dump(
                exclude={
                    "claim_id",
                    "rendered_text",
                    "text_verified",
                }
            )
        )
        binding = bindings.get(result.claim_id)
        assessment = None
        reason = "EVIDENCE_NOT_REQUESTED" if binding is None else "CLAIM_NOT_GROUNDED"
        allowed = result.text_verified
        if binding is not None:
            resolved = resolutions[result.claim_id]
            integrity = resolved.rules[0].result
            applicability = None
            if result.outcome == "GROUNDED" and prepared.fingerprint is not None:
                evaluated = evaluate_evidence_context(
                    evidence_request(request, binding), current, history, resolved
                )
                # Resolution failures have no context assessment, rather than
                # falsely presenting the resolution status as applicability.
                if integrity.status == "RESOLVED":
                    applicability = evaluated.rules[0]
                allowed = integrity.status == "RESOLVED" and (
                    applicability is not None and applicability.status == "RESOLVED"
                )
                reason = "SYNTHETIC_CONTEXT_MATCHED" if allowed else "EVIDENCE_WITHHELD"
            else:
                allowed = False
            assessment = EvidenceAssessment(
                binding=binding,
                approval_manifest_id=resolved.manifest_id,
                approval_manifest_version=resolved.manifest_version,
                approval_manifest_hash=resolved.manifest_hash,
                integrity=integrity,
                applicability=applicability,
            )
        if invalid_state:
            allowed, reason = False, "ACCEPTED_STATE_INVALID"
        if prepared.fingerprint is None:
            allowed, reason = False, "CATALOG_UNAVAILABLE_OR_INVALID"
        output.append(
            EvidenceAwareClaimResult.model_validate(
                dict(
                    claim_id=result.claim_id,
                    grounding=summary,
                    evidence=assessment,
                    reason_code=reason,
                    rendered_text=result.rendered_text if allowed else None,
                    text_verified=allowed,
                    checks_run=(COMPOSITION,),
                    checks_not_applicable=(
                        (SkippedCheck(rule_id="EV-CONTEXT-APPLICABILITY-01", reason_code=reason),)
                        if binding is None or assessment is None or assessment.applicability is None
                        else ()
                    ),
                )
            )
        )
    return EvidenceAwareRecord(
        draft_id=grounding.draft_id,
        patient_id=patient_id,
        policy_version=POLICY_VERSION,
        grounding_policy_version=grounding.policy_version,
        evidence_policy_version=EVIDENCE_POLICY,
        renderer_version=grounding.renderer_version,
        catalogue_fingerprint=prepared.fingerprint,
        snapshot=grounding.snapshot,
        consistency_ruleset_version=grounding.consistency_ruleset_version,
        consistency_findings=grounding.consistency_findings,
        claims=tuple(output),
    )
