"""Deterministic record selection, not general language or clinical understanding."""

import re
from uuid import UUID, uuid5

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.consistency import FactVersion
from app.schemas.draft_verification import (
    DraftClaim,
    DraftVerificationRequest,
    RestatementContent,
    SourceAttribution,
)
from app.schemas.evidence import RuleSelection
from app.schemas.evidence_aware_draft import ClaimEvidenceBinding, EvidenceAwareDraftRequest
from app.schemas.record_questions import QuestionStatus, RecordQuestion
from app.services.consistency import load_accepted_state
from app.services.consistency_evaluator import ConsistencyInputError, evaluate_consistency
from app.services.evidence_catalog import load_catalog

ALIASES = {
    "hba1c": "hba1c",
    "glucose": "glucose",
    "creatinine": "creatinine",
    "egfr": "egfr",
    "urine albumin creatinine ratio": "urine_albumin_creatinine_ratio",
}
QUESTIONS = {
    "what confirmed clinical information is recorded": "records",
    "what medications are currently recorded": "medication",
    "what medications are recorded": "medication",
    "what symptoms or relevant history have been recorded": "symptoms_history",
    "what symptoms have been recorded": "symptom",
    "what relevant history has been recorded": "history",
}


def parse_question(question: str) -> tuple[str, str | None]:
    normalized = " ".join(question.lower().strip().removesuffix("?").split())
    if normalized in QUESTIONS:
        return QUESTIONS[normalized], None
    match = re.fullmatch(
        r"what is (?:my|the|this patient's|the patient's) latest recorded (?:value for )?(.+)",
        normalized,
    )
    if match and match[1] in ALIASES:
        return "latest", ALIASES[match[1]]
    if re.search(
        r"\b(diagnosis|diagnose|treatment|treat|prescribe|dose|should|stop|start|"
        r"increase|decrease|change|safe|normal|interpret)\b",
        normalized,
    ):
        return "refuse", None
    return "unsupported", None


def build_question(
    patient_id: UUID,
    payload: RecordQuestion,
    current: list[FactVersion],
    history: list[FactVersion],
) -> EvidenceAwareDraftRequest | QuestionStatus:
    """Bind only immutable accepted versions. No selection/completeness claim is rendered."""
    try:
        checked = evaluate_consistency(patient_id, current, history)
    except ConsistencyInputError:
        raise HTTPException(409, "Accepted question context unavailable") from None
    if any(f.category == "INTEGRITY_ERROR" for f in checked.findings):
        raise HTTPException(409, "Accepted question context unavailable")
    intent, code = parse_question(payload.question)
    if intent in {"refuse", "unsupported"}:
        return QuestionStatus(
            status="REFUSE" if intent == "refuse" else "CLARIFY",
            reason_code="CLINICAL_DECISION_UNSUPPORTED"
            if intent == "refuse"
            else "QUESTION_UNSUPPORTED",
        )
    if payload.include_synthetic_provenance and (intent != "latest" or code != "hba1c"):
        return QuestionStatus(status="CLARIFY", reason_code="SYNTHETIC_CONTEXT_UNSUPPORTED")
    selected = [
        r
        for r in current
        if intent == "records"
        or (intent == "latest" and r.fact.kind == "laboratory" and r.fact.code == code)
        or (intent == "symptoms_history" and r.fact.kind in {"symptom", "history"})
        or r.fact.kind == intent
    ]
    if not selected:
        return QuestionStatus(status="CLARIFY", reason_code="NO_MATCHING_RECORDS")
    if intent == "latest":
        latest = max(r.observed_at for r in selected)
        selected = [r for r in selected if r.observed_at == latest]
        if len(selected) != 1:
            return QuestionStatus(status="CLARIFY", reason_code="LATEST_AMBIGUOUS")
    if len(selected) > 32:
        return QuestionStatus(status="CLARIFY", reason_code="TOO_MANY_RECORDS")
    if payload.include_synthetic_provenance and any(
        r.fact.kind != "laboratory" or r.fact.unit != "%" for r in selected
    ):
        return QuestionStatus(status="CLARIFY", reason_code="SYNTHETIC_CONTEXT_UNSUPPORTED")
    claims = tuple(
        DraftClaim(
            claim_id=uuid5(payload.operation_id, str(r.version_id)),
            patient_id=patient_id,
            claim_type="FACT_RESTATEMENT",
            fact_version_ids=(r.version_id,),
            source_attribution=SourceAttribution(
                author_id=r.author_id, author_role=r.author_role, category=r.attribution
            ),
            proposed_content=RestatementContent(
                fact=r.fact.model_dump(), observed_at=r.observed_at
            ),
        )
        for r in sorted(selected, key=lambda r: (r.observed_at, str(r.version_id)))
    )
    bindings: tuple[ClaimEvidenceBinding, ...] = ()
    if payload.include_synthetic_provenance:
        try:
            catalog = load_catalog()
            row = selected[0]
            rule_id = (
                "synthetic-patient-lab-context"
                if row.attribution == "patient_reported"
                else "synthetic-practitioner-lab-context"
            )
            rule = next(
                r
                for r in catalog.registry.rules
                if (r.rule_id, r.version) == (rule_id, "fixture-1")
            )
            bindings = (
                ClaimEvidenceBinding(
                    claim_id=claims[0].claim_id,
                    rule=RuleSelection(
                        rule_id=rule.rule_id, rule_version=rule.version, evidence=rule.evidence
                    ),
                    approval_manifest_id=catalog.manifest.manifest_id,
                    approval_manifest_version=catalog.manifest.version,
                    intended_use="synthetic-context-testing",
                ),
            )
        except (ValueError, OSError, StopIteration):
            raise HTTPException(503, "Synthetic evidence unavailable") from None
    return EvidenceAwareDraftRequest(
        draft=DraftVerificationRequest(
            draft_id=uuid5(payload.operation_id, "record-question"),
            patient_id=patient_id,
            claims=claims,
        ),
        bindings=bindings,
    )


async def prepare_question(
    db: AsyncSession, actor_id: UUID, patient_id: UUID, payload: RecordQuestion
) -> EvidenceAwareDraftRequest | QuestionStatus:
    current, history = await load_accepted_state(db, actor_id, patient_id)
    return build_question(patient_id, payload, current, history)
