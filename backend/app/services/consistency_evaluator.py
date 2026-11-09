"""Pure structural rules. No clocks, database, mutation, evidence or care advice."""

from collections import defaultdict
from collections.abc import Sequence
from itertools import combinations
from uuid import UUID

from app.schemas.consistency import (
    ConsistencyEvaluation,
    FactVersion,
    Finding,
    SkippedCheck,
    Snapshot,
)

RULESET_VERSION = "longitudinal-structural-v1"
RULES = (
    "membership",
    "current_selection",
    "chain",
    "attribution",
    "provenance",
    "chronology",
    "comparison",
)


class ConsistencyInputError(ValueError):
    """Fixed message only; never echo patient identities or input values."""


def evaluate_consistency(
    patient_id: UUID, current: Sequence[FactVersion], history: Sequence[FactVersion]
) -> ConsistencyEvaluation:
    """History must contain ALL accepted versions for this patient, including current.

    Mixed-patient inputs raise before findings/snapshot creation. Nonaccepted rows
    cause membership findings and short-circuit every content/chain comparison.
    Input order does not affect output. No caller-owned objects are modified.
    """
    rows = (*current, *history)
    if any(row.patient_id != patient_id for row in rows):
        raise ConsistencyInputError("Patient scope mismatch")
    findings: set[tuple[str, str, str, tuple[UUID, ...]]] = set()
    skipped: list[SkippedCheck] = []
    run = ["membership"]

    def add(rule: str, reason: str, *ids: UUID, category: str = "INTEGRITY_ERROR") -> None:
        findings.add((rule, category, reason, tuple(sorted(set(ids), key=str))))

    def result() -> ConsistencyEvaluation:
        return ConsistencyEvaluation(
            patient_id=patient_id,
            ruleset_version=RULESET_VERSION,
            snapshot=Snapshot(
                current_version_ids=tuple(sorted({r.version_id for r in current}, key=str)),
                history_version_ids=tuple(sorted({r.version_id for r in history}, key=str)),
            ),
            findings=tuple(
                Finding.model_validate(
                    dict(rule_id=r, category=c, reason_code=s, fact_version_ids=i)
                )
                for r, c, s, i in sorted(
                    findings, key=lambda f: (f[0], f[1], f[2], tuple(map(str, f[3])))
                )
            ),
            checks_run=tuple(run),
            checks_not_applicable=tuple(skipped),
        )

    for row in rows:
        if row.status != "CONFIRMED":
            add("membership", "NON_ACCEPTED_INPUT", row.version_id)
    if findings:
        skipped.extend(
            SkippedCheck(rule_id=r, reason_code="ACCEPTED_INPUT_REQUIRED") for r in RULES[1:]
        )
        return result()

    run.extend(RULES[1:-1])
    by_id: dict[UUID, FactVersion] = {}
    chains: dict[UUID, list[FactVersion]] = defaultdict(list)
    for row in sorted(history, key=lambda r: (str(r.version_id), r.model_dump_json())):
        if row.version_id in by_id:
            add("chain", "DUPLICATE_VERSION_ID", row.version_id)
            continue
        by_id[row.version_id] = row
        chains[row.fact_id].append(row)
    for row in rows:
        expected = "patient_reported" if row.author_role == "patient" else "practitioner_managed"
        if row.attribution != expected:
            add("attribution", "AUTHOR_CATEGORY_MISMATCH", row.version_id)
        if row.entry_method == "STRUCTURED":
            if row.confirmed_by_id is not None or row.confirmed_at is not None:
                add("provenance", "STRUCTURED_CONFIRMATION_MARKER", row.version_id)
        elif row.confirmed_by_id != row.author_id or row.confirmed_at is None:
            add("provenance", "EXTRACTED_CONFIRMATION_MISSING_OR_WRONG_SPEAKER", row.version_id)
        if row.confirmed_at is not None and row.confirmed_at > row.entered_at:
            add("chronology", "CONFIRMATION_AFTER_ACCEPTED_ENTRY", row.version_id)
        if row.version > 1 and not (row.correction_reason and row.correction_reason.strip()):
            add("provenance", "CORRECTION_REASON_REQUIRED", row.version_id)

    for chain in chains.values():
        ordered = sorted(chain, key=lambda r: (r.version, str(r.version_id)))
        roots = [r for r in ordered if r.version == 1 and r.version_id == r.fact_id]
        if len(roots) != 1:
            add("chain", "ROOT_MISSING_OR_INVALID", *(r.version_id for r in ordered))
        for index, row in enumerate(ordered):
            if row.version == 1:
                if (
                    row.version_id != row.fact_id
                    or row.previous_version_id is not None
                    or row.previous_version is not None
                    or row.correction_reason is not None
                ):
                    add("chain", "INVALID_ROOT", row.version_id)
                continue
            if row.version < 1:
                add("chain", "INVALID_VERSION_NUMBER", row.version_id)
                continue
            parent = by_id.get(row.previous_version_id) if row.previous_version_id else None
            if parent is None:
                # Do not echo an untrusted parent UUID: it might belong to another patient.
                add("chain", "ACCEPTED_PARENT_MISSING", row.version_id)
                continue
            if parent.fact_id != row.fact_id or parent.attribution != row.attribution:
                add("chain", "PARENT_IDENTITY_MISMATCH", row.version_id, parent.version_id)
            if row.previous_version != parent.version or row.version <= parent.version:
                add("chain", "PARENT_VERSION_MISMATCH", row.version_id, parent.version_id)
            if index == 0 or ordered[index - 1].version_id != parent.version_id:
                add("chain", "PARENT_NOT_PREVIOUS_ACCEPTED_VERSION", row.version_id)
            if row.entered_at < parent.entered_at:
                add(
                    "chronology",
                    "CORRECTION_ENTRY_BEFORE_PARENT",
                    row.version_id,
                    parent.version_id,
                )
        if len({r.version for r in chain}) != len(chain):
            add("chain", "DUPLICATE_VERSION_NUMBER", *(r.version_id for r in chain))
        selected = [r for r in current if r.fact_id == ordered[0].fact_id]
        if len(selected) != 1 or selected[0].version_id != ordered[-1].version_id:
            add("current_selection", "CURRENT_NOT_LATEST_ACCEPTED", *(r.version_id for r in chain))
    for row in current:
        if row.version_id not in by_id:
            add("current_selection", "CURRENT_ABSENT_FROM_HISTORY", row.version_id)
        elif row != by_id[row.version_id]:
            add("current_selection", "CURRENT_HISTORY_CONTENT_MISMATCH", row.version_id)

    if findings:
        skipped.append(SkippedCheck(rule_id="comparison", reason_code="VALID_STATE_REQUIRED"))
        return result()

    # A comparison is attempted only for same-kind/key observations at the same
    # instant in DISTINCT roots. Different-time observations are never conflicts.
    pairs = []
    for left, right in combinations(sorted(current, key=lambda r: str(r.version_id)), 2):
        a, b = left.fact, right.fact
        key_a = getattr(a, "code", getattr(a, "medication_name", getattr(a, "category", None)))
        key_b = getattr(b, "code", getattr(b, "medication_name", getattr(b, "category", None)))
        if (
            left.fact_id != right.fact_id
            and a.kind == b.kind
            and key_a == key_b
            and left.observed_at == right.observed_at
        ):
            pairs.append((left, right))
    if not pairs:
        skipped.append(
            SkippedCheck(rule_id="comparison", reason_code="NO_MATCHING_OBSERVATION_PAIRS")
        )
        return result()
    run.append("comparison")
    for left, right in pairs:
        if (
            left.fact == right.fact
            and left.provenance == right.provenance
            and left.provenance.reference
            and left.author_id == right.author_id
            and left.attribution == right.attribution
            and left.entry_method == right.entry_method
        ):
            add(
                "comparison",
                "IDENTICAL_CONTENT_TIME_AUTHOR_AND_SOURCE",
                left.version_id,
                right.version_id,
                category="POSSIBLE_DUPLICATE",
            )
        else:
            unit_a = getattr(left.fact, "unit", getattr(left.fact, "dose_unit", None))
            unit_b = getattr(right.fact, "unit", getattr(right.fact, "dose_unit", None))
            reason = (
                "DIFFERENT_UNITS_NO_CONVERSION"
                if unit_a != unit_b
                else "EVENT_IDENTITY_NOT_ESTABLISHED"
            )
            add("comparison", reason, left.version_id, right.version_id, category="NOT_COMPARABLE")
    return result()
