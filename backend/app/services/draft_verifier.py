"""Pure independent FACT_RESTATEMENT checking against an authoritative snapshot."""

from collections.abc import Sequence
from uuid import UUID

from app.schemas.consistency import FactVersion, SkippedCheck
from app.schemas.draft_verification import (
    ClaimResult,
    DraftClaim,
    DraftVerificationRecord,
    DraftVerificationRequest,
    RestatementContent,
)
from app.services.consistency_evaluator import evaluate_consistency
from app.services.fact_renderer import RENDERER_VERSION, decimal_text, render_restatement

POLICY_VERSION = "structured-fact-restatement-v1"
STRUCTURE = "DV-STRUCTURE-01"
REFERENCE = "DV-FACT-REFERENCE-01"
ATTRIBUTION = "DV-ATTRIBUTION-01"
CONTENT = "DV-FACT-CONTENT-01"
RENDER = "DV-RENDER-01"
CHECKS = (STRUCTURE, REFERENCE, ATTRIBUTION, CONTENT, RENDER)


class DraftInputError(ValueError):
    """Sanitized batch failure; no inaccessible identifiers or values."""


def _result(
    claim: DraftClaim,
    outcome: str,
    reason: str,
    run: tuple[str, ...],
    row: FactVersion | None = None,
    *,
    redact_references: bool = False,
) -> ClaimResult:
    rendered = render_restatement(row) if outcome == "GROUNDED" and row is not None else None
    return ClaimResult.model_validate(
        dict(
            claim_id=claim.claim_id,
            outcome=outcome,
            reason_code=reason,
            fact_version_ids=()
            if redact_references
            else tuple(sorted(set(claim.fact_version_ids), key=str)),
            rendered_text=rendered,
            text_verified=rendered is not None,
            checks_run=run,
            checks_not_applicable=tuple(
                SkippedCheck(rule_id=r, reason_code="PREREQUISITE_NOT_MET")
                for r in CHECKS
                if r not in run
            ),
        )
    )


def _verify(claim: DraftClaim, by_id: dict[UUID, FactVersion]) -> ClaimResult:
    if claim.claim_type != "FACT_RESTATEMENT":
        return _result(claim, "UNSUPPORTED", "CLAIM_TYPE_UNSUPPORTED", (STRUCTURE,))
    if len(claim.fact_version_ids) != 1:
        return _result(claim, "INVALID", "ONE_FACT_REFERENCE_REQUIRED", (STRUCTURE,))
    if not isinstance(claim.proposed_content, RestatementContent):
        return _result(claim, "INVALID", "STRUCTURED_CONTENT_REQUIRED", (STRUCTURE,))
    if claim.source_attribution is None:
        return _result(claim, "INVALID", "SOURCE_ATTRIBUTION_REQUIRED", (STRUCTURE,))
    row = by_id[claim.fact_version_ids[0]]
    attribution = claim.source_attribution
    run: tuple[str, ...] = (STRUCTURE, REFERENCE, ATTRIBUTION)
    if (attribution.author_id, attribution.author_role, attribution.category) != (
        row.author_id,
        row.author_role,
        row.attribution,
    ):
        return _result(claim, "UNGROUNDED", "SOURCE_ATTRIBUTION_MISMATCH", run)
    run = (*run, CONTENT)
    proposed, accepted = claim.proposed_content.fact, row.fact
    # Compare typed JSON payloads: frozen draft subclasses intentionally differ
    # from ORM input schema classes. Numeric spelling is normalized explicitly.
    left, right = proposed.model_dump(mode="json"), accepted.model_dump(mode="json")
    if proposed.kind != accepted.kind:
        return _result(claim, "UNGROUNDED", "FACT_KIND_MISMATCH", run)
    if proposed.kind == "laboratory" and accepted.kind == "laboratory":
        if proposed.code != accepted.code:
            return _result(claim, "UNGROUNDED", "FACT_CODE_MISMATCH", run)
        if proposed.unit != accepted.unit:
            return _result(claim, "UNGROUNDED", "FACT_UNIT_MISMATCH", run)
        if proposed.value != accepted.value:
            return _result(claim, "UNGROUNDED", "FACT_VALUE_MISMATCH", run)
        left["value"], right["value"] = decimal_text(proposed.value), decimal_text(accepted.value)
    elif proposed.kind == "medication" and accepted.kind == "medication":
        if proposed.dose_value is not None:
            left["dose_value"] = decimal_text(proposed.dose_value)
        if accepted.dose_value is not None:
            right["dose_value"] = decimal_text(accepted.dose_value)
    if left != right:
        return _result(claim, "UNGROUNDED", "FACT_CONTENT_MISMATCH", run)
    if claim.proposed_content.observed_at != row.observed_at:
        return _result(claim, "UNGROUNDED", "OBSERVATION_TIME_MISMATCH", run)
    return _result(claim, "GROUNDED", "FACT_RESTATEMENT_MATCH", (*run, RENDER), row)


def verify_fact_claims(
    patient_id: UUID,
    current: Sequence[FactVersion],
    history: Sequence[FactVersion],
    request: DraftVerificationRequest,
) -> DraftVerificationRecord:
    """All references are preflighted: unavailable IDs withhold ALL batch text.

    Unsupported claims are never interpreted as prose. Current/history are loaded
    by the patient service, never obtained from a drafting provider's assertions.
    """
    if request.patient_id != patient_id or any(c.patient_id != patient_id for c in request.claims):
        raise DraftInputError("Draft patient scope unavailable")
    consistency = evaluate_consistency(patient_id, current, history)
    by_id = {r.version_id: r for r in current if r.status == "CONFIRMED"}
    unavailable = any(i not in by_id for c in request.claims for i in c.fact_version_ids)
    claims = sorted(request.claims, key=lambda c: str(c.claim_id))
    invalid = any(f.category == "INTEGRITY_ERROR" for f in consistency.findings)
    results = tuple(
        _result(
            c, "UNVERIFIABLE", "REFERENCED_STATE_UNAVAILABLE", (REFERENCE,), redact_references=True
        )
        if unavailable
        else _result(c, "INVALID", "ACCEPTED_STATE_INVALID", (REFERENCE,))
        if invalid
        else _verify(c, by_id)
        for c in claims
    )
    return DraftVerificationRecord(
        draft_id=request.draft_id,
        patient_id=patient_id,
        policy_version=POLICY_VERSION,
        renderer_version=RENDERER_VERSION,
        snapshot=consistency.snapshot,
        consistency_ruleset_version=consistency.ruleset_version,
        consistency_findings=consistency.findings,
        claims=results,
    )
