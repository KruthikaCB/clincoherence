"""Natural-language selection over accepted records; independently verified restatements."""

import re
from datetime import UTC
from uuid import UUID, uuid5

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
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
from app.services.question_planner import QuestionPlan, plan_question

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
    *,
    plan: QuestionPlan | None = None,
) -> EvidenceAwareDraftRequest | QuestionStatus:
    """Bind only immutable accepted versions. No selection/completeness claim is rendered."""
    try:
        checked = evaluate_consistency(patient_id, current, history)
    except ConsistencyInputError:
        raise HTTPException(409, "Accepted question context unavailable") from None
    if any(f.category == "INTEGRITY_ERROR" for f in checked.findings):
        raise HTTPException(409, "Accepted question context unavailable")
    intent, code = (
        (plan.intent, plan.code) if plan is not None else parse_question(payload.question)
    )
    if intent in {"refuse", "unsupported"}:
        return QuestionStatus(
            status="REFUSE" if intent == "refuse" else "CLARIFY",
            reason_code="CLINICAL_DECISION_UNSUPPORTED"
            if intent == "refuse"
            else "QUESTION_UNSUPPORTED",
        )
    if payload.include_synthetic_provenance and (
        intent != "latest" or code != "hba1c" or (plan is not None and plan.position != 0)
    ):
        return QuestionStatus(status="CLARIFY", reason_code="SYNTHETIC_CONTEXT_UNSUPPORTED")
    selected = [
        r
        for r in current
        if intent == "records"
        or (intent == "latest" and r.fact.kind == "laboratory" and r.fact.code == code)
        or (intent == "symptoms_history" and r.fact.kind in {"symptom", "history"})
        or r.fact.kind == intent
    ]
    if plan is not None and plan.medication_name is not None:
        selected = [
            r
            for r in selected
            if r.fact.kind == "medication"
            and r.fact.medication_name.casefold() == plan.medication_name.strip().casefold()
        ]
    if not selected:
        return QuestionStatus(status="CLARIFY", reason_code="NO_MATCHING_RECORDS")
    if intent == "latest":
        observations = sorted({r.observed_at.astimezone(UTC) for r in selected}, reverse=True)
        position = plan.position if plan is not None else 0
        if position >= len(observations):
            return QuestionStatus(status="CLARIFY", reason_code="NO_MATCHING_RECORDS")
        latest = observations[position]
        selected = [r for r in selected if r.observed_at.astimezone(UTC) == latest]
        # Collapse duplicate reports of one observation for answer selection only.
        # Decimal equality and aware datetime equality preserve exact values and
        # equivalent timezones. Different units are never converted or conflated.
        values = {(r.fact.value, r.fact.unit) for r in selected if r.fact.kind == "laboratory"}
        if len(values) != 1:
            return QuestionStatus(status="CLARIFY", reason_code="LATEST_AMBIGUOUS")
        selected = [min(selected, key=lambda row: str(row.version_id))]
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
    if settings.LLM_PROVIDER == "ollama":
        # Validate before sending even category metadata to the local model.
        try:
            checked = evaluate_consistency(patient_id, current, history)
        except ConsistencyInputError:
            raise HTTPException(409, "Accepted question context unavailable") from None
        if any(f.category == "INTEGRITY_ERROR" for f in checked.findings):
            raise HTTPException(409, "Accepted question context unavailable")
        if re.search(
            r"\b(?:should|recommend\w*|diagnose|normal|safe|increase|decrease|interpret\w*)\b"
            r"|\bprescribe\s+(?:me|a|an)\b",
            payload.question,
            re.IGNORECASE,
        ):
            return QuestionStatus(status="REFUSE", reason_code="CLINICAL_DECISION_UNSUPPORTED")
        # Never hold patient/consent locks while the CPU model is running.
        await db.rollback()
        plan = await plan_question(payload, current)
        fresh, fresh_history = await load_accepted_state(db, actor_id, patient_id)

        def snapshot(rows: list[FactVersion]) -> list[str]:
            return sorted(r.model_dump_json() for r in rows)

        if snapshot(current) != snapshot(fresh) or snapshot(history) != snapshot(fresh_history):
            raise HTTPException(409, "Patient records changed; ask again using fresh state")
        return build_question(patient_id, payload, fresh, fresh_history, plan=plan)
    return build_question(patient_id, payload, current, history)
