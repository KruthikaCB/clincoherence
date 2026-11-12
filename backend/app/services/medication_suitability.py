"""Read-only synthetic educational label checks. No prescribing/positive clearance.

No external calls, dose calculation, M8 dependency or persistence. Existing locks
remain held through ClinicalResponse release. Unrecognized history stays UNKNOWN.
"""

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PatientProfile
from app.schemas.consistency import FactVersion
from app.schemas.medication_suitability import (
    LabelCatalog, MedicationAssessment, MedicationSuitabilityResponse, RuleCheck,
)
from app.services.clinical_facts import lock_patient_scope
from app.services.consistency import load_accepted_state
from app.services.consistency_evaluator import ConsistencyInputError, evaluate_consistency

CATALOG_PATH = Path(__file__).resolve().parents[1] / "fixtures/medication_suitability/catalog.v1.json"
CATALOG_SHA256 = "4ee65baa3343e8a76074206553681f129340911d4253a8ca472a27ad46dc55ec"
MEDICATIONS = ("metformin", "empagliflozin", "semaglutide")
LIMITATIONS = (
    "Synthetic educational scenario only; not a live clinical recommendation or prescribing instruction.",
    "Speaker-confirmed records are not separately clinician-approved. Migration 0011 is not used.",
    "Only the named label checks run. No positive suitability or safety clearance is produced.",
    "Observation dates describe recorded scenarios, not present-day renal function, adherence or completeness.",
    "No ADA treatment ranking, interaction screen, age/pregnancy assessment or comprehensive contraindication review is implemented.",
    "Derived on request; no new audit event is persisted. Exact source fact versions and catalog hash are returned.",
)
MISSING = {
    "metformin": ("Current renal assessment and medication reconciliation", "Metformin hypersensitivity and metabolic-acidosis assessment", "Contrast exposure, hepatic/hypoxic illness and alcohol-risk review"),
    "empagliflozin": ("Current renal function, volume status and treatment indication", "Hypersensitivity and ketoacidosis-risk assessment", "Heart-failure/CKD context and medication reconciliation"),
    "semaglutide": ("Semaglutide hypersensitivity assessment", "Pancreatitis, retinopathy, gastroparesis and dehydration-risk review", "Medication reconciliation and applicable reproductive/age context"),
}
CAUTIONS = {
    "metformin": ("The label includes lactic-acidosis precautions; these checks do not assess all contributing risks.",),
    "empagliflozin": ("Renal glycemic-control limits must not be generalized to heart-failure or CKD indications.", "The label also requires attention to volume depletion and ketoacidosis risk."),
    "semaglutide": ("The Ozempic label also addresses pancreatitis, retinopathy, dehydration-related kidney injury and severe gastroparesis.",),
}
# Exact, documented templates. Negation is never inferred from arbitrary prose.
HISTORY_FIELDS = (
    ("prior_event", "personal history of medullary thyroid carcinoma", "no personal history of medullary thyroid carcinoma", "Personal MTC history"),
    ("family_history", "family history of medullary thyroid carcinoma", "no family history of medullary thyroid carcinoma", "Family MTC history"),
    ("other", "multiple endocrine neoplasia type 2", "no multiple endocrine neoplasia type 2", "MEN2 history"),
)


def load_catalog() -> LabelCatalog | None:
    """Unavailable/tampered evidence cannot enable a rule. No network fallback."""
    try:
        raw = CATALOG_PATH.read_bytes()
        if hashlib.sha256(raw).hexdigest() != CATALOG_SHA256:
            return None
        return LabelCatalog.model_validate_json(raw)
    except (OSError, ValueError):
        return None


def _latest_lab(facts: list[FactVersion], code: str, unit: str):
    rows = [r for r in facts if r.fact.kind == "laboratory" and r.fact.code == code]
    if not rows:
        return None, (), f"{code}: UNKNOWN — no accepted observation"
    stamp = max(r.observed_at for r in rows)
    rows = [r for r in rows if r.observed_at == stamp]
    ids = tuple(r.version_id for r in rows)
    values = {(r.fact.value, r.fact.unit) for r in rows}
    if len(values) != 1:
        return None, ids, f"{code}: UNKNOWN — conflicting latest observations"
    value, observed_unit = next(iter(values))
    if observed_unit != unit or value <= 0:
        return None, ids, f"{code}: UNKNOWN — unsupported unit or nonpositive value"
    return value, ids, None


def assess_records(
    patient_id: UUID, current: list[FactVersion], history: list[FactVersion],
    catalog: LabelCatalog | None, *, now: datetime,
) -> MedicationSuitabilityResponse:
    checked = evaluate_consistency(patient_id, current, history)
    if any(f.category == "INTEGRITY_ERROR" for f in checked.findings):
        raise ConsistencyInputError("Invalid accepted snapshot")
    if len(current) > 128 or any(r.provenance.data_origin != "synthetic" for r in history):
        raise ConsistencyInputError("Synthetic snapshot unavailable")
    current = sorted(current, key=lambda r: str(r.version_id))
    eligible = [r for r in current if r.observed_at <= now and r.entered_at <= now]
    future = len(eligible) != len(current)
    egfr, renal_ids, renal_missing = _latest_lab(eligible, "egfr", "mL/min/1.73m2")
    _, _, hba_missing = _latest_lab(eligible, "hba1c", "%")
    diagnosis = [r for r in eligible if r.fact.kind == "diagnosis" and r.fact.code == "type_2_diabetes"]
    common_missing = ["Separate clinician approval (not implemented in this demo path)"]
    if not diagnosis:
        common_missing.append("Type 2 Diabetes diagnosis: UNKNOWN")
    if renal_missing:
        common_missing.append(renal_missing)
    if hba_missing:
        common_missing.append(hba_missing)
    if future:
        common_missing.append("Future-dated observations or entries: UNKNOWN; rules withheld")
    if not any(r.fact.kind == "medication" for r in eligible):
        common_missing.append("Medication records: UNKNOWN; absence is not medication-free status")

    history_missing, positive_history, recognized_history, history_conflict = [], [], [], False
    for category, positive, negative, label in HISTORY_FIELDS:
        matches = [r for r in eligible if r.fact.kind == "history" and r.fact.category == category
                   and " ".join(r.fact.description.casefold().split()) in {positive, negative}]
        values = {" ".join(r.fact.description.casefold().split()) == positive for r in matches}
        recognized_history.extend(r.version_id for r in matches)
        if len(values) != 1:
            history_missing.append(f"{label}: UNKNOWN" + (" — conflicting statements" if values else " — not explicitly documented"))
            history_conflict |= len(values) > 1
        elif True in values:
            positive_history.extend(r.version_id for r in matches)

    assessments = []
    for medication in MEDICATIONS:
        status = "INSUFFICIENT_INFORMATION"
        reasons = ["No positive suitability assessment: the available checks do not establish comprehensive eligibility."]
        missing = [*common_missing, *MISSING[medication]]
        if medication == "semaglutide":
            missing.extend(history_missing)
        checks, sources = [], []
        if catalog is None:
            missing.append("Verified local evidence catalog: UNAVAILABLE")
            reasons = ["Evidence is unavailable or failed integrity checks; no clinical rule was evaluated."]
        else:
            rules = [rule for rule in catalog.rules if rule.medication == medication]
            sources = [source for source in catalog.sources if source.source_id in {r.source_id for r in rules}]
            for rule in rules:
                outcome = "UNKNOWN"
                ids = renal_ids if rule.condition != "explicit_mtc_or_men2" else tuple(recognized_history)
                if not future and diagnosis:
                    if rule.condition == "egfr_lt_30" and egfr is not None:
                        outcome = "TRIGGERED" if egfr < Decimal("30") else "NOT_TRIGGERED"
                    elif rule.condition == "egfr_30_through_45" and egfr is not None:
                        outcome = "TRIGGERED" if Decimal("30") <= egfr <= Decimal("45") else "NOT_TRIGGERED"
                    elif rule.condition == "explicit_mtc_or_men2" and not history_conflict:
                        if positive_history:
                            outcome = "TRIGGERED"
                        elif not history_missing:
                            outcome = "NOT_TRIGGERED"
                checks.append(RuleCheck(rule=rule, outcome=outcome, fact_version_ids=ids))
                if outcome == "TRIGGERED":
                    if rule.rule_id in {"MET-RENAL-30-v1", "SEMA-MTC-MEN2-v1"}:
                        status = "NOT_SUITABLE_UNDER_DOCUMENTED_RULE"
                    elif status != "NOT_SUITABLE_UNDER_DOCUMENTED_RULE":
                        status = "CAUTION_REVIEW_REQUIRED"
                    reasons.append("Recorded synthetic scenario matches: " + rule.description)
                elif outcome == "NOT_TRIGGERED":
                    reasons.append(f"{rule.rule_id}: this limited check is not triggered; this is not clearance.")
        assessments.append(MedicationAssessment(
            medication=medication, status=status, reasoning=tuple(reasons),
            cautions=CAUTIONS[medication] if catalog else ("Clinical rule checks are unavailable; clinician review is required.",), missing_information=tuple(missing),
            fact_version_ids=tuple(r.version_id for r in current), rule_checks=tuple(checks),
            evidence=tuple(sources),
        ))
    return MedicationSuitabilityResponse(
        patient_id=patient_id, assessed_at=now, facts=tuple(current), assessments=tuple(assessments),
        catalog_version=catalog.catalog_version if catalog else None,
        catalog_sha256=CATALOG_SHA256 if catalog else None,
        evidence_review_status=catalog.review_status if catalog else "UNAVAILABLE",
        limitations=LIMITATIONS,
    )


async def patient_assessment(db: AsyncSession, account_id: UUID, patient_id: UUID):
    account = await lock_patient_scope(db, account_id, patient_id)
    if account.role != "practitioner":
        raise HTTPException(403, "Practitioner access required")
    demo = await db.scalar(select(PatientProfile.is_demo).where(PatientProfile.id == patient_id))
    if demo is not True:
        raise HTTPException(409, "Only synthetic demo records support educational assessment")
    current, history = await load_accepted_state(db, account_id, patient_id)
    try:
        return assess_records(patient_id, current, history, load_catalog(), now=datetime.now(UTC))
    except (ConsistencyInputError, ValueError):
        raise HTTPException(409, "Synthetic accepted record context unavailable") from None
