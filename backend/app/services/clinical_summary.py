"""Read-only T2DM record summary using the existing 4A, 4B and 4D checks."""

from collections.abc import Sequence
from datetime import UTC, datetime
from itertools import product
from typing import Any, cast
from uuid import UUID, uuid5

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.clinical_summary import (
    ClinicalSummary,
    ClinicalSummaryRun,
    SummaryChange,
    SummaryObservation,
    SummaryRecord,
)
from app.schemas.consistency import FactVersion
from app.schemas.draft_verification import (
    DraftClaim,
    DraftVerificationRequest,
    RestatementContent,
    SourceAttribution,
)
from app.schemas.laboratory_comparison import VersionPair
from app.services.authorization import authorize_patient_scope
from app.services.consistency import load_accepted_state
from app.services.consistency_evaluator import ConsistencyInputError, evaluate_consistency
from app.services.draft_verifier import POLICY_VERSION as VERIFICATION_POLICY
from app.services.draft_verifier import verify_fact_claims
from app.services.fact_renderer import RENDERER_VERSION, decimal_text
from app.services.laboratory_evaluator import POLICY_VERSION as LABORATORY_POLICY
from app.services.laboratory_evaluator import evaluate_laboratory_pairs

POLICY_VERSION = "recorded-t2dm-summary-v1"


class SummaryInputError(ValueError):
    """Sanitized failure: never expose inaccessible identifiers or values."""


def build_clinical_summary(
    patient_id: UUID, current: Sequence[FactVersion], history: Sequence[FactVersion]
) -> ClinicalSummary:
    """Pure internal evaluator, not a public request or a clinical release gate.

    Duplicate values collapse only within one observation time and exact unit;
    every author/version/provenance reference is retained. Different times remain
    different observations. Ambiguous latest points are never replaced by older
    unambiguous ones. No unit conversions or interpretation of change occur.
    """
    try:
        current = [FactVersion.model_validate(r.model_dump(warnings=False)) for r in current]
        history = [FactVersion.model_validate(r.model_dump(warnings=False)) for r in history]
    except ValidationError:
        raise SummaryInputError("Accepted summary context unavailable") from None
    checked = evaluate_consistency(patient_id, current, history)
    if any(f.category == "INTEGRITY_ERROR" for f in checked.findings):
        raise SummaryInputError("Accepted summary context unavailable")

    def laboratories(code: str) -> list[FactVersion]:
        return [r for r in current if r.fact.kind == "laboratory" and r.fact.code == code]

    hba1c, egfr = laboratories("hba1c"), laboratories("egfr")
    latest_egfr = max((r.observed_at for r in egfr), default=None)
    egfr = [r for r in egfr if r.observed_at == latest_egfr]
    medications = sorted(
        (r for r in current if r.fact.kind == "medication"),
        key=lambda r: (r.observed_at, str(r.version_id)),
    )
    selected = {r.version_id: r for r in (*hba1c, *egfr, *medications)}
    records: dict[UUID, SummaryRecord] = {}
    draft_id = uuid5(
        patient_id,
        POLICY_VERSION + ":" + ":".join(map(str, checked.snapshot.history_version_ids)),
    )
    ordered = sorted(selected.values(), key=lambda r: str(r.version_id))
    # Reuse the verifier's bounded request contract without truncating a summary.
    for start in range(0, len(ordered), 32):
        batch = ordered[start : start + 32]
        claims = tuple(
            DraftClaim(
                claim_id=uuid5(draft_id, str(r.version_id)),
                patient_id=patient_id,
                claim_type="FACT_RESTATEMENT",
                fact_version_ids=(r.version_id,),
                source_attribution=SourceAttribution(
                    author_id=r.author_id, author_role=r.author_role, category=r.attribution
                ),
                proposed_content=RestatementContent(
                    fact=cast(Any, r.fact.model_dump()), observed_at=r.observed_at
                ),
            )
            for r in batch
        )
        verified = verify_fact_claims(
            patient_id,
            current,
            history,
            DraftVerificationRequest(draft_id=draft_id, patient_id=patient_id, claims=claims),
        )
        for claim in verified.claims:
            if claim.outcome != "GROUNDED" or not claim.text_verified:
                raise SummaryInputError("Accepted summary context unavailable")
            version_id = claim.fact_version_ids[0]
            records[version_id] = SummaryRecord(source=selected[version_id], verification=claim)

    def observations(rows: Sequence[FactVersion]) -> tuple[SummaryObservation, ...]:
        result = []
        for stamp in sorted({r.observed_at.astimezone(UTC) for r in rows}, reverse=True):
            group = sorted(
                (r for r in rows if r.observed_at == stamp), key=lambda r: str(r.version_id)
            )
            values = {(r.fact.value, r.fact.unit) for r in group if r.fact.kind == "laboratory"}
            unique = len(values) == 1
            value, unit = next(iter(values)) if unique else (None, None)
            result.append(
                SummaryObservation(
                    observed_at=stamp,
                    status="RECORDED" if unique else "AMBIGUOUS",
                    value=value,
                    unit=unit,
                    records=tuple(records[r.version_id] for r in group),
                )
            )
        return tuple(result)

    points, renal = observations(hba1c), observations(egfr)
    latest = points[0] if points else None
    previous = points[1] if len(points) > 1 else None
    change = SummaryChange(direction="UNAVAILABLE", reason_code="PREVIOUS_OBSERVATION_MISSING")
    if latest is not None and previous is not None:
        change = SummaryChange(direction="NOT_COMPARABLE", reason_code="AMBIGUOUS_OBSERVATION")
        if latest.status == previous.status == "RECORDED":
            # All duplicate provenance paths must be comparable; do not select a
            # convenient author/category pair while ignoring conflicting paths.
            pairs = [
                VersionPair(
                    first_version_id=a.source.version_id, second_version_id=b.source.version_id
                )
                for a, b in product(previous.records, latest.records)
            ]
            comparison = evaluate_laboratory_pairs(patient_id, current, history, pairs)
            if comparison.not_comparable:
                change = SummaryChange(
                    direction="NOT_COMPARABLE", reason_code=comparison.not_comparable[0].reason_code
                )
            else:
                difference = comparison.comparisons[0]
                delta = difference.numeric_difference
                change = SummaryChange(
                    direction="INCREASED"
                    if delta > 0
                    else "DECREASED"
                    if delta < 0
                    else "UNCHANGED",
                    reason_code="RECORDED_VALUES_COMPARED",
                    comparison=difference,
                )

    def observation_text(label: str, point: SummaryObservation) -> str:
        stamp = point.observed_at.isoformat().replace("+00:00", "Z")
        if point.status == "AMBIGUOUS":
            return (
                f"{label} at {stamp}: different recorded values or units; no single value selected."
            )
        assert point.value is not None and point.unit is not None
        return f"{label} at {stamp}: {decimal_text(point.value)} {point.unit}."

    missing: list[str] = []
    text = [observation_text("HbA1c", point) for point in points]
    if latest is None:
        missing.append("hba1c")
        text.append("No accepted HbA1c observation is recorded.")
    if previous is None:
        missing.append("previous_hba1c")
        text.append("Previous HbA1c unavailable.")
    text.append(f"Recorded HbA1c change: {change.direction} ({change.reason_code}).")
    if renal:
        text.append(observation_text("Latest eGFR", renal[0]))
    else:
        missing.append("egfr")
        text.append("No accepted eGFR observation is recorded.")
    text.append("Recorded medications do not establish current use or adherence.")
    text.extend(records[r.version_id].verification.rendered_text or "" for r in medications)
    if not medications:
        missing.append("medications")
        text.append("No accepted medication records are available.")
    return ClinicalSummary(
        patient_id=patient_id,
        policy_version=POLICY_VERSION,
        verification_policy_version=VERIFICATION_POLICY,
        renderer_version=RENDERER_VERSION,
        laboratory_policy_version=LABORATORY_POLICY,
        snapshot=checked.snapshot,
        hba1c_observations=points,
        latest_hba1c=latest,
        previous_hba1c=previous,
        hba1c_change=change,
        latest_egfr=renal[0] if renal else None,
        medications=tuple(records[r.version_id] for r in medications),
        missing_information=tuple(missing),
        text="\n".join(text),
    )


async def summarize_patient_records(
    db: AsyncSession, account_id: UUID, patient_id: UUID
) -> ClinicalSummaryRun:
    """Internal only. Caller owns transaction/locks through protected consumption.

    No commits, flushes, clinical writes or external calls. A detached/cached
    summary is not release-authorized; future HTTP use requires separately
    approved audited release, not direct serialization of this internal result.
    """
    current, history = await load_accepted_state(db, account_id, patient_id)
    try:
        summary = build_clinical_summary(patient_id, current, history)
    except (SummaryInputError, ConsistencyInputError):
        raise HTTPException(409, "Accepted summary context unavailable") from None
    await authorize_patient_scope(db, account_id, patient_id)
    return ClinicalSummaryRun(evaluated_at=datetime.now(UTC), summary=summary)
