"""Explicit-pair arithmetic only. No inferred event identity or clinical policy."""

from collections.abc import Sequence
from decimal import Context, Decimal, localcontext
from uuid import UUID

from app.schemas.consistency import FactVersion, SkippedCheck
from app.schemas.laboratory_comparison import (
    LaboratoryComparison,
    LaboratoryEvaluation,
    NotComparable,
    VersionPair,
)
from app.services.consistency_evaluator import evaluate_consistency

POLICY_VERSION = "laboratory-explicit-pairs-v1"
ELIGIBILITY = "LC-LAB-ELIGIBILITY-01"
DIFFERENCE = "LC-LAB-DIFFERENCE-01"


class LaboratoryInputError(ValueError):
    """Fixed error; no requested identifier or value may escape in error text."""


def exact_difference(later: Decimal, earlier: Decimal) -> Decimal:
    """Independent of the caller's decimal precision, traps and rounding mode.

    Existing finite, bounded laboratory decimals need at most the aligned
    coefficient width plus a carry digit. Canonical output removes insignificant
    trailing zeros and negative zero without rounding or unit conversion.
    """
    exponents = (later.as_tuple().exponent, earlier.as_tuple().exponent)
    assert all(isinstance(e, int) for e in exponents)
    exponent = min(int(e) for e in exponents)
    precision = max(1, max(later.adjusted(), earlier.adjusted()) - exponent + 2)
    with localcontext(Context(prec=precision)):
        difference = later - earlier
    if not difference:
        return Decimal(0)
    text = format(difference, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return Decimal(text)


def evaluate_laboratory_pairs(
    patient_id: UUID,
    current: Sequence[FactVersion],
    history: Sequence[FactVersion],
    pairs: Sequence[VersionPair],
) -> LaboratoryEvaluation:
    """Revalidate with 4A. Never accept caller-supplied claims of a valid snapshot.

    Pair order is irrelevant; repeated/reversed pairs collapse. All requested
    IDs must resolve to current CONFIRMED versions before any output is returned.
    Structural integrity failures block arithmetic; 4A findings stay separate.
    """
    consistency = evaluate_consistency(patient_id, current, history)
    by_id = {row.version_id: row for row in current if row.status == "CONFIRMED"}
    keys = sorted(
        {tuple(sorted((p.first_version_id, p.second_version_id), key=str)) for p in pairs},
        key=lambda pair: tuple(map(str, pair)),
    )
    if any(a not in by_id or b not in by_id for a, b in keys):
        raise LaboratoryInputError("Requested versions unavailable")
    canonical = tuple(VersionPair(first_version_id=a, second_version_id=b) for a, b in keys)
    comparisons: list[LaboratoryComparison] = []
    unavailable: list[NotComparable] = []
    run: list[str] = []
    skipped: list[SkippedCheck] = []
    if not keys:
        skipped.extend(
            SkippedCheck(rule_id=r, reason_code="NO_PAIRS_REQUESTED")
            for r in (ELIGIBILITY, DIFFERENCE)
        )
    else:
        run.append(ELIGIBILITY)
        if any(f.category == "INTEGRITY_ERROR" for f in consistency.findings):
            skipped.append(
                SkippedCheck(rule_id=DIFFERENCE, reason_code="VALID_ACCEPTED_STATE_REQUIRED")
            )
        else:
            for a, b in keys:
                left, right = by_id[a], by_id[b]
                first, second = left.fact, right.fact
                reason = None
                if first.kind != "laboratory" or second.kind != "laboratory":
                    reason = "LABORATORY_FACTS_REQUIRED"
                elif left.fact_id == right.fact_id:
                    reason = "DISTINCT_FACT_ROOTS_REQUIRED"
                elif first.code != second.code:
                    reason = "LABORATORY_CODE_MISMATCH"
                elif first.unit != second.unit:
                    reason = "UNIT_MISMATCH_NO_CONVERSION"
                elif left.author_id != right.author_id:
                    reason = "AUTHOR_MISMATCH"
                elif left.attribution != right.attribution:
                    reason = "ATTRIBUTION_MISMATCH"
                elif left.observed_at == right.observed_at:
                    reason = "DISTINCT_OBSERVATION_TIMES_REQUIRED"
                else:
                    earlier, later = (
                        (left, right) if left.observed_at < right.observed_at else (right, left)
                    )
                    # The kind guards above establish these are laboratory payloads.
                    assert earlier.fact.kind == "laboratory" and later.fact.kind == "laboratory"
                    comparisons.append(
                        LaboratoryComparison(
                            earlier_version_id=earlier.version_id,
                            later_version_id=later.version_id,
                            numeric_difference=exact_difference(
                                later.fact.value, earlier.fact.value
                            ),
                            unit=first.unit,
                        )
                    )
                if reason is not None:
                    unavailable.append(
                        NotComparable.model_validate(
                            dict(reason_code=reason, fact_version_ids=(a, b))
                        )
                    )
            if comparisons:
                run.append(DIFFERENCE)
            else:
                skipped.append(
                    SkippedCheck(rule_id=DIFFERENCE, reason_code="NO_ELIGIBLE_REQUESTED_PAIRS")
                )
    return LaboratoryEvaluation(
        patient_id=patient_id,
        policy_version=POLICY_VERSION,
        consistency=consistency,
        requested_pairs=canonical,
        comparisons=tuple(comparisons),
        not_comparable=tuple(unavailable),
        checks_run=tuple(run),
        checks_not_applicable=tuple(skipped),
    )
