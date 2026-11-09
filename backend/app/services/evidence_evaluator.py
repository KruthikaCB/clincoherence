"""Pure synthetic fixture applicability; not clinical verification."""

from collections.abc import Sequence
from typing import TYPE_CHECKING

from app.schemas.consistency import FactVersion, SkippedCheck
from app.schemas.evidence import EvidenceEvaluation, RuleEvaluation, Status, VerificationRequest
from app.services.consistency_evaluator import evaluate_consistency
from app.services.evidence_catalog import CONTEXT, PreparedEvidence, PreparedRule

if TYPE_CHECKING:
    from uuid import UUID

POLICY_VERSION = "synthetic-evidence-context-v1"


class EvidenceInputError(ValueError):
    """Fixed unavailable error; no foreign/requested identifiers in messages."""


def _context(prepared: PreparedRule, selected: Sequence[FactVersion]) -> RuleEvaluation:
    result, definition = prepared.result, prepared.definition
    if result.status != "RESOLVED" or definition is None:
        return result
    status: Status = "RESOLVED"
    reason = "SYNTHETIC_CONTEXT_MATCHED"
    matched: set[UUID] = set()
    if not selected:
        status, reason = "INSUFFICIENT_CONTEXT", "REQUIRED_FACT_MISSING"
    else:
        for requirement in definition.requirements:
            kind = [r for r in selected if r.fact.kind == requirement.kind]
            code = [
                r for r in kind if r.fact.kind == "laboratory" and r.fact.code == requirement.code
            ]
            unit = [
                r for r in code if r.fact.kind == "laboratory" and r.fact.unit == requirement.unit
            ]
            qualified = [
                r
                for r in unit
                if all(getattr(r, q.field) == q.equals for q in requirement.qualifiers)
            ]
            if not kind:
                status, reason = "NOT_APPLICABLE", "FACT_KIND_MISMATCH"
            elif not code:
                status, reason = "NOT_APPLICABLE", "FACT_CODE_MISMATCH"
            elif not unit:
                status, reason = "NOT_APPLICABLE", "UNIT_MISMATCH_NO_CONVERSION"
            elif not qualified:
                status, reason = "NOT_APPLICABLE", "STRUCTURED_QUALIFIER_MISMATCH"
            else:
                matched.update(r.version_id for r in qualified)
            if status != "RESOLVED":
                matched.clear()
                break
    return RuleEvaluation(
        rule_id=result.rule_id,
        rule_version=result.rule_version,
        status=status,
        reason_code=reason,
        evidence=result.evidence,
        matched_fact_version_ids=tuple(sorted(matched, key=str)),
        checks_run=(*result.checks_run, CONTEXT),
        checks_not_applicable=(),
    )


def evaluate_evidence_context(
    request: VerificationRequest,
    current: Sequence[FactVersion],
    history: Sequence[FactVersion],
    prepared: PreparedEvidence,
) -> EvidenceEvaluation:
    """Prepared evidence is internal trusted resolver output, not caller input.

    Full 4A validation is reused. Only explicitly selected current accepted rows
    supply context. No field is inferred from descriptions, history prose or proposals.
    """
    consistency = evaluate_consistency(request.patient_id, current, history)
    by_id = {r.version_id: r for r in current if r.status == "CONFIRMED"}
    ids = sorted(set(request.fact_version_ids), key=str)
    if any(i not in by_id for i in ids):
        raise EvidenceInputError("Requested accepted context unavailable")
    selected = [by_id[i] for i in ids]
    invalid = any(f.category == "INTEGRITY_ERROR" for f in consistency.findings)
    rules = []
    for item in prepared.rules:
        if invalid and item.result.status == "RESOLVED":
            blocked = item.result.model_copy(
                update={
                    "status": "ERROR",
                    "reason_code": "ACCEPTED_STATE_INVALID",
                    "checks_not_applicable": (
                        SkippedCheck(rule_id=CONTEXT, reason_code="VALID_ACCEPTED_STATE_REQUIRED"),
                    ),
                }
            )
            rules.append(blocked)
        else:
            rules.append(_context(item, selected))
    if not rules:
        status: Status = "ERROR" if prepared.manifest_hash is None else "NOT_APPLICABLE"
        reason = (
            "CATALOG_UNAVAILABLE_OR_INVALID"
            if prepared.manifest_hash is None
            else "NO_RULES_REQUESTED"
        )
    else:
        status, reason = "RESOLVED", "SYNTHETIC_CONTEXT_MATCHED"
        priorities: tuple[tuple[Status, str], ...] = (
            ("ERROR", "CHECK_ERROR"),
            ("UNAVAILABLE", "EVIDENCE_UNAVAILABLE"),
            ("INSUFFICIENT_CONTEXT", "REQUIRED_CONTEXT_MISSING"),
            ("NOT_APPLICABLE", "RULE_NOT_APPLICABLE"),
        )
        for candidate, code in priorities:
            if any(r.status == candidate for r in rules):
                status = candidate  # narrowed by validation below
                reason = code
                break
    return EvidenceEvaluation.model_validate(
        dict(
            patient_id=request.patient_id,
            policy_version=POLICY_VERSION,
            snapshot=consistency.snapshot,
            consistency_ruleset_version=consistency.ruleset_version,
            consistency_findings=consistency.findings,
            approval_manifest_id=prepared.manifest_id,
            approval_manifest_version=prepared.manifest_version,
            approval_manifest_hash=prepared.manifest_hash,
            status=status,
            reason_code=reason,
            rules=tuple(rules),
        )
    )
