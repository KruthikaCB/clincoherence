"""Pure technical gate over trusted original requests and fresh 4E records."""

from typing import get_args

from pydantic import ValidationError

from app.schemas.consistency import SkippedCheck
from app.schemas.draft_verification import ClaimResult, DraftClaim, RestatementContent
from app.schemas.evidence_aware_draft import (
    EvidenceAwareClaimResult,
    EvidenceAwareDraftRequest,
    EvidenceAwareRecord,
)
from app.schemas.verification_gate import (
    ClaimGateDecision,
    Decision,
    GateDecisionRecord,
    GatedRecordText,
    GateInput,
    GateVerificationReference,
    Reason,
)
from app.services.consistency_evaluator import RULESET_VERSION
from app.services.draft_verifier import POLICY_VERSION as GROUNDING_POLICY
from app.services.evidence_aware_evaluator import POLICY_VERSION as COMPOSITION_POLICY
from app.services.evidence_catalog import canonical_hash
from app.services.evidence_evaluator import POLICY_VERSION as EVIDENCE_POLICY
from app.services.fact_renderer import RENDERER_VERSION

POLICY_VERSION = "structured-record-gate-v1"
COVERAGE, POLICY, BATCH = "GATE-COVERAGE-01", "GATE-POLICY-01", "GATE-BATCH-01"
SKIPPED = (SkippedCheck(rule_id="GATE-WARN-01", reason_code="WARNING_POLICY_NOT_CONFIGURED"),)
# Fail closed on new upstream reasons until explicitly reviewed for this gate version.
GROUNDING_REASONS = set(get_args(ClaimResult.model_fields["reason_code"].annotation))
EVIDENCE_REASONS = {
    "RESOLVED": {"SYNTHETIC_EVIDENCE_RESOLVED", "SYNTHETIC_CONTEXT_MATCHED"},
    "UNAVAILABLE": {
        "MANIFEST_VERSION_MISMATCH",
        "RULE_VERSION_UNAVAILABLE",
        "INTENDED_USE_NOT_APPROVED",
        "EVIDENCE_BINDING_MISMATCH",
        "EVIDENCE_NOT_APPROVED",
        "APPROVAL_WITHDRAWN",
        "SOURCE_UNAVAILABLE",
        "SOURCE_EDITION_MISMATCH",
        "SOURCE_HASH_MISMATCH",
        "UNIT_UNAVAILABLE",
        "INVALID_LOCATOR",
        "UNIT_HASH_MISMATCH",
    },
    "NOT_APPLICABLE": {
        "FACT_KIND_MISMATCH",
        "FACT_CODE_MISMATCH",
        "UNIT_MISMATCH_NO_CONVERSION",
        "STRUCTURED_QUALIFIER_MISMATCH",
    },
    "INSUFFICIENT_CONTEXT": {"REQUIRED_FACT_MISSING"},
    "ERROR": {"RULE_HASH_MISMATCH", "CATALOG_UNAVAILABLE_OR_INVALID", "ACCEPTED_STATE_INVALID"},
}


class GateInputError(ValueError):
    """Malformed original request or mixed scope: no patient decision record."""


def request_digest(request: EvidenceAwareDraftRequest) -> str:
    data = request.model_dump(mode="json")
    data["draft"]["claims"] = sorted(data["draft"]["claims"], key=lambda c: c["claim_id"])
    data["bindings"] = sorted(data["bindings"], key=lambda b: b["claim_id"])
    for binding in data["bindings"]:
        binding["rule"]["evidence"] = sorted(binding["rule"]["evidence"], key=canonical_hash)
    return canonical_hash(data)


def _coverage(request: EvidenceAwareDraftRequest, record: EvidenceAwareRecord) -> bool:
    expected = {c.claim_id for c in request.draft.claims}
    ids = [c.claim_id for c in record.claims]
    current, history = record.snapshot.current_version_ids, record.snapshot.history_version_ids
    if (
        len(ids) != len(set(ids))
        or set(ids) != expected
        or len(set(current)) != len(current)
        or len(set(history)) != len(history)
        or not set(current) <= set(history)
    ):
        return False
    bindings = {b.claim_id: b for b in request.bindings}
    claims = {c.claim_id: c for c in request.draft.claims}
    for result in record.claims:
        if result.evidence is None:
            if result.claim_id in bindings:
                return False
        elif result.evidence.binding != bindings.get(result.claim_id):
            return False
        refs = result.grounding.fact_version_ids
        if len(set(refs)) != len(refs):
            return False
        if result.grounding.outcome == "UNVERIFIABLE":
            if refs:
                return False
        elif set(refs) != set(claims[result.claim_id].fact_version_ids):
            return False
        if result.grounding.outcome == "GROUNDED" and not set(refs) <= set(current):
            return False
    return True


def _claim_decision(claim: DraftClaim, result: EvidenceAwareClaimResult) -> tuple[Decision, Reason]:
    ground, evidence = result.grounding, result.evidence
    if ground.reason_code not in GROUNDING_REASONS:
        return "REFUSE", "VERIFICATION_CONTRACT_INVALID"
    if claim.claim_type != "FACT_RESTATEMENT":
        return "REFUSE", "CLAIM_TYPE_UNSUPPORTED"
    if isinstance(claim.proposed_content, str):
        return "REFUSE", "UNVERIFIED_PROSE"
    if ground.outcome == "UNVERIFIABLE":
        return "REFUSE", "ACCEPTED_REFERENCE_UNAVAILABLE"
    if ground.reason_code == "ACCEPTED_STATE_INVALID":
        return "REFUSE", "ACCEPTED_STATE_INVALID"
    if ground.outcome != "GROUNDED" and (
        result.reason_code
        != ("EVIDENCE_NOT_REQUESTED" if evidence is None else "CLAIM_NOT_GROUNDED")
        or (evidence is not None and evidence.applicability is not None)
    ):
        return "REFUSE", "VERIFICATION_CONTRACT_INVALID"
    if evidence is not None:
        integrity, context = evidence.integrity, evidence.applicability
        for assessment in (integrity, context):
            if (
                assessment is not None
                and assessment.reason_code not in EVIDENCE_REASONS[assessment.status]
            ):
                return "REFUSE", "VERIFICATION_CONTRACT_INVALID"
            if assessment is not None and (assessment.rule_id, assessment.rule_version) != (
                evidence.binding.rule.rule_id,
                evidence.binding.rule.rule_version,
            ):
                return "REFUSE", "VERIFICATION_CONTRACT_INVALID"
        if integrity.status != "RESOLVED":
            reason: Reason = "REQUIRED_EVIDENCE_UNAVAILABLE"
            if integrity.status != "UNAVAILABLE" or integrity.reason_code in {
                "EVIDENCE_BINDING_MISMATCH",
                "SOURCE_EDITION_MISMATCH",
                "SOURCE_HASH_MISMATCH",
                "INVALID_LOCATOR",
                "UNIT_HASH_MISMATCH",
                "MANIFEST_VERSION_MISMATCH",
            }:
                reason = "EVIDENCE_REFERENCE_INVALID"
            return "REFUSE", reason
        if integrity.reason_code != "SYNTHETIC_EVIDENCE_RESOLVED":
            return "REFUSE", "VERIFICATION_CONTRACT_INVALID"
        if context is not None and context.status != "RESOLVED":
            # Current single-fact contracts cannot identify recoverable missing
            # evidence context. Never invent a clarification for it.
            return "REFUSE", "EVIDENCE_CONTEXT_NOT_APPLICABLE"
    if ground.outcome == "UNGROUNDED":
        return "REFUSE", "FACT_RESTATEMENT_MISMATCH"
    if ground.outcome == "INVALID":
        recoverable = (
            (ground.reason_code == "ONE_FACT_REFERENCE_REQUIRED" and not claim.fact_version_ids)
            or (
                ground.reason_code == "STRUCTURED_CONTENT_REQUIRED"
                and claim.proposed_content is None
            )
            or (
                ground.reason_code == "SOURCE_ATTRIBUTION_REQUIRED"
                and claim.source_attribution is None
            )
        )
        return (
            ("CLARIFY", "REQUIRED_INPUT_MISSING")
            if recoverable
            else (
                "REFUSE",
                "VERIFICATION_CONTRACT_INVALID",
            )
        )
    if ground.outcome != "GROUNDED" or ground.reason_code != "FACT_RESTATEMENT_MATCH":
        return "REFUSE", "VERIFICATION_CONTRACT_INVALID"
    if (
        len(claim.fact_version_ids) != 1
        or not isinstance(claim.proposed_content, RestatementContent)
        or claim.source_attribution is None
    ):
        return "REFUSE", "VERIFICATION_CONTRACT_INVALID"
    if evidence is not None:
        context = evidence.applicability
        if (
            context is None
            or context.reason_code != "SYNTHETIC_CONTEXT_MATCHED"
            or evidence.approval_manifest_hash is None
            or evidence.approval_manifest_id != evidence.binding.approval_manifest_id
            or evidence.approval_manifest_version != evidence.binding.approval_manifest_version
            or set(evidence.integrity.evidence) != set(evidence.binding.rule.evidence)
            or len(set(evidence.integrity.evidence)) != len(evidence.integrity.evidence)
            or set(context.evidence) != set(evidence.integrity.evidence)
            or len(set(context.evidence)) != len(context.evidence)
            or set(context.matched_fact_version_ids) != set(ground.fact_version_ids)
            or len(set(context.matched_fact_version_ids)) != len(context.matched_fact_version_ids)
        ):
            return "REFUSE", "VERIFICATION_CONTRACT_INVALID"
    if not result.text_verified or not result.rendered_text:
        return "REFUSE", "RENDERER_TEXT_UNVERIFIED"
    expected_reason = "EVIDENCE_NOT_REQUESTED" if evidence is None else "SYNTHETIC_CONTEXT_MATCHED"
    if result.reason_code != expected_reason:
        return "REFUSE", "VERIFICATION_CONTRACT_INVALID"
    return "PASS", (
        "ACCEPTED_RECORD_RESTATEMENT" if evidence is None else "SYNTHETIC_REFERENCE_CHECKS_COMPLETE"
    )


def evaluate_gate(inputs: GateInput) -> GateDecisionRecord:
    """Trusted internal boundary only; Pydantic types are not proof of verification."""
    try:
        request = EvidenceAwareDraftRequest.model_validate(
            inputs.request.model_dump(warnings=False)
        )
    except ValidationError:
        raise GateInputError("Invalid original gate request") from None
    if any(c.patient_id != request.draft.patient_id for c in request.draft.claims):
        raise GateInputError("Gate scope unavailable")
    record = None
    global_reason: Reason | None = None
    try:
        record = EvidenceAwareRecord.model_validate(inputs.verification.model_dump(warnings=False))
    except ValidationError:
        global_reason = "VERIFICATION_CONTRACT_INVALID"
    if record is not None and (
        record.patient_id != request.draft.patient_id or record.draft_id != request.draft.draft_id
    ):
        raise GateInputError("Gate scope unavailable")
    if record is not None:
        if not _coverage(request, record):
            global_reason = "CLAIM_COVERAGE_INVALID"
        elif (
            record.policy_version != COMPOSITION_POLICY
            or record.grounding_policy_version != GROUNDING_POLICY
            or record.evidence_policy_version != EVIDENCE_POLICY
            or record.renderer_version != RENDERER_VERSION
            or record.consistency_ruleset_version != RULESET_VERSION
        ):
            global_reason = "VERIFICATION_CONTRACT_INVALID"
        elif record.catalogue_fingerprint is None:
            global_reason = "CATALOGUE_INTEGRITY_INVALID"
        elif any(f.category == "INTEGRITY_ERROR" for f in record.consistency_findings):
            global_reason = "ACCEPTED_STATE_INVALID"
    results = {r.claim_id: r for r in record.claims} if record is not None else {}
    decisions = []
    for claim in sorted(request.draft.claims, key=lambda c: str(c.claim_id)):
        result = results.get(claim.claim_id)
        decision, reason = (
            ("REFUSE", global_reason)
            if global_reason is not None
            else _claim_decision(claim, results[claim.claim_id])
        )
        decisions.append(
            ClaimGateDecision.model_validate(
                dict(
                    claim_id=claim.claim_id,
                    decision=decision,
                    reason_codes=(reason,),
                    grounding=result.grounding
                    if result is not None and global_reason is None
                    else None,
                    evidence=result.evidence
                    if result is not None and global_reason is None
                    else None,
                    checks_run=(COVERAGE, POLICY),
                    checks_not_applicable=SKIPPED,
                )
            )
        )
    reasons: set[Reason] = {r for c in decisions for r in c.reason_codes}
    if global_reason:
        reasons.add(global_reason)
    prose = request.draft.unverified_text is not None and len(request.draft.unverified_text) > 0
    if prose:
        reasons.add("UNRESTRICTED_PROSE_PRESENT")
    if global_reason or prose or any(c.decision == "REFUSE" for c in decisions):
        batch: Decision = "REFUSE"
    elif not decisions or any(c.decision == "CLARIFY" for c in decisions):
        batch = "CLARIFY"
        if not decisions:
            reasons.add("NO_SUPPORTED_CLAIMS_SELECTED")
    else:
        batch = "PASS"
    reference = None
    if record is not None and global_reason in {
        None,
        "ACCEPTED_STATE_INVALID",
        "CATALOGUE_INTEGRITY_INVALID",
    }:
        reference = GateVerificationReference(
            record_digest=canonical_hash(record.model_dump(mode="json")),
            composition_policy_version=record.policy_version,
            grounding_policy_version=record.grounding_policy_version,
            evidence_policy_version=record.evidence_policy_version,
            renderer_version=record.renderer_version,
            consistency_ruleset_version=record.consistency_ruleset_version,
            snapshot=record.snapshot,
            consistency_findings=record.consistency_findings,
            catalogue_fingerprint=record.catalogue_fingerprint,
        )
    return GateDecisionRecord(
        draft_id=request.draft.draft_id,
        patient_id=request.draft.patient_id,
        policy_version=POLICY_VERSION,
        request_digest=request_digest(request),
        verification=reference,
        claims=tuple(decisions),
        decision=batch,
        reason_codes=tuple(sorted(reasons)),
        rendered_records=tuple(
            GatedRecordText(claim_id=c.claim_id, text=results[c.claim_id].rendered_text)
            for c in decisions
        )
        if batch == "PASS"
        else (),
        checks_run=(COVERAGE, POLICY, BATCH),
        checks_not_applicable=SKIPPED,
    )
