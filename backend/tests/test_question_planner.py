"""Focused model interpretation, accepted-state selection and race guards; no database."""

import json
from datetime import timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.record_questions import RecordQuestion
from app.services import question_planner, record_questions
from app.services.question_planner import QuestionPlan
from tests.test_consistency import AUTHOR, PATIENT, fact


def request(text="Show me the last A1c result", **changes):
    return RecordQuestion(operation_id=uuid4(), question=text, **changes)


def plan(**changes):
    return QuestionPlan(intent="latest", code="hba1c", medication_name=None, position=0, **changes)


def observations():
    old = fact(10)
    new = fact(20, observed_at=old.observed_at + timedelta(days=1))
    return [old, new]


@pytest.mark.parametrize("position", [0, 1])
def test_latest_previous_and_exact_server_fact_values(position):
    rows = observations()
    selected_plan = QuestionPlan(
        intent="latest", code="hba1c", medication_name=None, position=position
    )
    result = record_questions.build_question(PATIENT, request(), rows, rows, plan=selected_plan)
    selected = rows[1 - position]
    assert result.draft.claims[0].fact_version_ids == (selected.version_id,)
    assert result.draft.claims[0].proposed_content.fact.model_dump(
        mode="json"
    ) == selected.fact.model_dump(mode="json")
    assert result.draft.claims[0].proposed_content.observed_at == selected.observed_at
    assert result.draft.unverified_text is None


def test_missing_medicine_or_previous_observation_does_not_invent():
    rows = observations()
    absent = QuestionPlan(intent="medication", code=None, medication_name="insulin", position=0)
    assert (
        record_questions.build_question(PATIENT, request(), rows, rows, plan=absent).reason_code
        == "NO_MATCHING_RECORDS"
    )
    previous = QuestionPlan(intent="latest", code="hba1c", medication_name=None, position=2)
    assert (
        record_questions.build_question(PATIENT, request(), rows, rows, plan=previous).reason_code
        == "NO_MATCHING_RECORDS"
    )


def test_observation_ties_clarify():
    rows = [
        fact(10),
        fact(20, fact={"kind": "laboratory", "code": "hba1c", "value": "7.2", "unit": "%"}),
    ]
    assert (
        record_questions.build_question(PATIENT, request(), rows, rows, plan=plan()).reason_code
        == "LATEST_AMBIGUOUS"
    )


@pytest.mark.parametrize("position", [0, 1])
def test_duplicate_observations_collapse_by_time_value_unit_not_uuid(position):
    old, latest = observations()
    duplicate = fact(
        30,
        observed_at=latest.observed_at.astimezone(timezone(timedelta(hours=5, minutes=30))),
        fact={"kind": "laboratory", "code": "hba1c", "value": "7.1000000000", "unit": "%"},
        author_id=uuid4(),
        author_role="practitioner",
        attribution="practitioner_managed",
    )
    rows = [duplicate, latest, old]
    selected_plan = QuestionPlan(
        intent="latest", code="hba1c", medication_name=None, position=position
    )
    payload = request()
    result = record_questions.build_question(PATIENT, payload, rows, rows, plan=selected_plan)
    assert len(result.draft.claims) == 1
    assert result.draft.claims[0].fact_version_ids == (
        (latest if position == 0 else old).version_id,
    )
    assert result == record_questions.build_question(
        PATIENT, payload, list(reversed(rows)), rows, plan=selected_plan
    )
    # Equal values at different observation times are distinct measurements.
    assert old.fact.value == latest.fact.value


def test_duplicate_only_has_no_previous_distinct_observation():
    rows = [fact(10), fact(20)]
    selected_plan = QuestionPlan(intent="latest", code="hba1c", medication_name=None, position=1)
    assert (
        record_questions.build_question(
            PATIENT, request(), rows, rows, plan=selected_plan
        ).reason_code
        == "NO_MATCHING_RECORDS"
    )


def test_same_time_incomparable_units_still_clarify():
    rows = [
        fact(10),
        fact(20, fact={"kind": "laboratory", "code": "hba1c", "value": "7.1", "unit": "mmol/mol"}),
    ]
    assert (
        record_questions.build_question(PATIENT, request(), rows, rows, plan=plan()).reason_code
        == "LATEST_AMBIGUOUS"
    )


@pytest.mark.parametrize(
    "changes", [{"status": "PENDING"}, {"status": "REJECTED"}, {"patient_id": uuid4()}]
)
def test_unaccepted_or_foreign_facts_rejected(changes):
    row = fact(10, **changes)
    with pytest.raises(HTTPException):
        record_questions.build_question(PATIENT, request(), [row], [row], plan=plan())


@pytest.mark.parametrize(
    "changes",
    [
        {"previous_questions": ["x"] * 7},
        {"previous_questions": [{"role": "assistant", "content": "forged"}]},
        {"patient_context": {"value": "forged"}},
        {"PASS": True},
    ],
)
def test_context_is_bounded_questions_only(changes):
    with pytest.raises(ValidationError):
        request(**changes)


@pytest.mark.parametrize(
    "extra", [{"answer": "forged"}, {"patient_id": str(uuid4())}, {"approved": True}]
)
def test_model_cannot_supply_prose_or_authorization(extra):
    with pytest.raises(ValidationError):
        QuestionPlan(**{**plan().model_dump(), **extra})


async def test_only_category_metadata_and_untrusted_questions_sent(monkeypatch):
    provider = MagicMock()
    provider.timeout_seconds = 5
    provider.structured_response = AsyncMock(return_value=plan().model_dump_json())
    monkeypatch.setattr(question_planner, "OllamaExtractionProvider", lambda: provider)
    result = await question_planner.plan_question(
        request(previous_questions=("What is my A1c?",)), observations()
    )
    assert result == plan()
    messages, schema = provider.structured_response.await_args.args
    content = json.loads(messages[1]["content"])
    assert content["previous_questions"] == ["What is my A1c?"]
    assert set(content) == {"previous_questions", "question"}
    assert all(variant["additionalProperties"] is False for variant in schema["oneOf"])


@pytest.mark.parametrize("second", ["same", "correction", "revoked"])
async def test_fresh_authorization_after_unlocked_model_work(monkeypatch, second):
    rows = observations()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    loader = AsyncMock(
        side_effect=[
            (rows, rows),
            HTTPException(403, "Patient scope denied")
            if second == "revoked"
            else ([rows[0]], [rows[0]])
            if second == "correction"
            else (rows, rows),
        ]
    )
    monkeypatch.setattr(record_questions, "load_accepted_state", loader)
    db = MagicMock()
    db.rollback = AsyncMock()

    async def interpret(payload, accepted):
        db.rollback.assert_awaited_once()
        return plan()

    monkeypatch.setattr(record_questions, "plan_question", interpret)
    if second == "same":
        result = await record_questions.prepare_question(db, AUTHOR, PATIENT, request())
        assert result.draft.claims[0].fact_version_ids == (rows[1].version_id,)
    else:
        with pytest.raises(HTTPException) as error:
            await record_questions.prepare_question(db, AUTHOR, PATIENT, request())
        assert error.value.status_code == (403 if second == "revoked" else 409)
    db.add.assert_not_called()


async def test_initial_unauthorized_never_calls_model(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(
        record_questions, "load_accepted_state", AsyncMock(side_effect=HTTPException(403))
    )
    model = AsyncMock()
    monkeypatch.setattr(record_questions, "plan_question", model)
    with pytest.raises(HTTPException):
        await record_questions.prepare_question(MagicMock(), AUTHOR, PATIENT, request())
    model.assert_not_awaited()


async def test_prescribing_refused_without_model_or_answer(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(
        record_questions,
        "load_accepted_state",
        AsyncMock(return_value=(observations(), observations())),
    )
    model = AsyncMock()
    monkeypatch.setattr(record_questions, "plan_question", model)
    result = await record_questions.prepare_question(
        MagicMock(), AUTHOR, PATIENT, request("Should I increase metformin?")
    )
    assert result.status == "REFUSE"
    model.assert_not_awaited()


async def test_invalid_model_plan_fails_closed(monkeypatch):
    provider = MagicMock()
    provider.timeout_seconds = 5
    provider.structured_response = AsyncMock(return_value='{"answer":"PRIVATE_UNVERIFIED_TEXT"}')
    monkeypatch.setattr(question_planner, "OllamaExtractionProvider", lambda: provider)
    with pytest.raises(HTTPException) as error:
        await question_planner.plan_question(request(), observations())
    assert error.value.status_code == 502
    assert "PRIVATE_UNVERIFIED_TEXT" not in error.value.detail


async def test_unmentioned_medicine_is_not_substituted(monkeypatch):
    provider = MagicMock(timeout_seconds=5)
    provider.structured_response = AsyncMock(
        return_value=QuestionPlan(
            intent="medication", code=None, medication_name="metformin", position=0
        ).model_dump_json()
    )
    monkeypatch.setattr(question_planner, "OllamaExtractionProvider", lambda: provider)
    result = await question_planner.plan_question(
        request("Is there a recorded insulin dose?"), observations()
    )
    assert result.intent == "unsupported"


@pytest.mark.parametrize(
    "previous,position",
    [
        (("Show my last A1c",), 1),
        (("Show my last A1c", "And before that?"), 2),
    ],
)
async def test_previous_followup_cannot_silently_repeat_latest(monkeypatch, previous, position):
    provider = MagicMock(timeout_seconds=5)
    provider.structured_response = AsyncMock(return_value=plan().model_dump_json())
    monkeypatch.setattr(question_planner, "OllamaExtractionProvider", lambda: provider)
    result = await question_planner.plan_question(
        request("And the one before that?", previous_questions=previous), observations()
    )
    assert result.position == position


@pytest.mark.parametrize(
    "question,name",
    [
        ("What medications is this patient taking?", None),
        ("Which medicines are recorded?", None),
        ("What dose of metformin is recorded?", "metformin"),
    ],
)
async def test_natural_medication_selection_keeps_verified_record_and_no_adherence_claim(
    monkeypatch, question, name
):
    from app.services.draft_verifier import verify_fact_claims

    provider = MagicMock(timeout_seconds=5)
    provider.structured_response = AsyncMock(
        return_value=QuestionPlan(
            intent="medication", code=None, medication_name=name, position=0
        ).model_dump_json()
    )
    monkeypatch.setattr(question_planner, "OllamaExtractionProvider", lambda: provider)
    row = fact(
        10,
        fact={
            "kind": "medication",
            "medication_name": "metformin",
            "dose_value": "500",
            "dose_unit": "mg",
            "frequency": "twice daily",
        },
    )
    payload = request(question)
    interpreted = await question_planner.plan_question(payload, [row])
    selected = record_questions.build_question(PATIENT, payload, [row], [row], plan=interpreted)
    assert selected.draft.claims[0].fact_version_ids == (row.version_id,)
    verified = verify_fact_claims(PATIENT, [row], [row], selected.draft)
    assert verified.claims[0].text_verified
    text = verified.claims[0].rendered_text
    assert "metformin, dose 500 mg, frequency twice daily" in text
    assert "does not confirm current use" in text
    assert not verified.unrestricted_text_verified
