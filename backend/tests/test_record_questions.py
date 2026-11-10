"""Deterministic question selection and existing audited-release bridge regression."""

import secrets
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import current_account, patient_scope
from app.api.v1 import record_questions as endpoint
from app.api.v1.verified_records import release_session_factory
from app.core.config import settings
from app.core.database import get_db
from app.main import create_app
from app.schemas.record_questions import QuestionStatus, RecordQuestion
from app.services.record_questions import build_question, parse_question
from tests.test_clinical_facts import race_environment as race_environment
from tests.test_consistency import AUTHOR, PATIENT, correction, fact
from tests.test_response_release import harness as harness
from tests.test_verified_records_api import database_bridge as database_bridge


@pytest.fixture(autouse=True)
def signing_configuration(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", secrets.token_urlsafe(48))
    monkeypatch.setattr(settings, "ALGORITHM", "HS256")


def question(text="What confirmed clinical information is recorded?"):
    return RecordQuestion(operation_id=uuid4(), question=text)


async def test_authentication_is_required_without_loading_records(monkeypatch):
    prepare = AsyncMock()
    monkeypatch.setattr(endpoint, "prepare_question", prepare)
    app = create_app()

    async def request_db():
        async with AsyncSession() as db:
            yield db

    app.dependency_overrides[get_db] = request_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
        result = await api.post(
            f"/api/v1/patients/{PATIENT}/record-questions", json=question().model_dump(mode="json")
        )
    assert result.status_code == 401 and "records" not in result.text
    prepare.assert_not_awaited()


async def test_safe_status_uses_fresh_protected_handoff(harness, monkeypatch):
    from types import SimpleNamespace

    from app.api.v1 import clinical_facts

    app = create_app()
    app.dependency_overrides[current_account] = lambda: SimpleNamespace(id=AUTHOR)
    app.dependency_overrides[patient_scope] = lambda: PATIENT
    app.dependency_overrides[release_session_factory] = lambda: harness["factory"]

    async def request_db():
        async with AsyncSession() as db:
            yield db

    app.dependency_overrides[get_db] = request_db
    monkeypatch.setattr(
        endpoint,
        "prepare_question",
        AsyncMock(return_value=QuestionStatus(status="CLARIFY", reason_code="NO_MATCHING_RECORDS")),
    )
    lock = AsyncMock()
    monkeypatch.setattr(clinical_facts, "lock_patient_scope", lock)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
        result = await api.post(
            f"/api/v1/patients/{PATIENT}/record-questions", json=question().model_dump(mode="json")
        )
    assert result.status_code == 200 and result.json()["reason_code"] == "NO_MATCHING_RECORDS"
    assert result.headers["cache-control"] == "no-store"
    assert lock.await_args.args[1:] == (AUTHOR, PATIENT)
    harness["prepare"].assert_not_awaited()


@pytest.mark.parametrize(
    "text,intent",
    [
        ("What confirmed clinical information is recorded?", "records"),
        ("What is my latest recorded value for hba1c?", "latest"),
        ("What is the latest recorded glucose?", "latest"),
        ("What medications are currently recorded?", "medication"),
        ("What symptoms or relevant history have been recorded?", "symptoms_history"),
        ("What symptoms have been recorded?", "symptom"),
        ("What relevant history has been recorded?", "history"),
        ("should I increase medication?", "refuse"),
        ("Is my hba1c normal?", "refuse"),
        ("What is my latest recorded hba1c and glucose?", "unsupported"),
        ("tell me anything about diabetes", "unsupported"),
    ],
)
def test_explicit_grammar(text, intent):
    assert parse_question(text)[0] == intent


@pytest.mark.parametrize("field", ["PASS", "patient_id", "verified_text", "request", "audit_id"])
def test_no_client_claims_or_authority(field):
    with pytest.raises(ValidationError):
        RecordQuestion.model_validate({**question().model_dump(), field: "forged"})


def test_latest_uses_observation_not_entry_and_keeps_decimal_attribution():
    old = fact(10)
    newer = fact(20, observed_at=old.observed_at + timedelta(days=1))
    latest = correction(
        newer,
        21,
        fact={"kind": "laboratory", "code": "hba1c", "value": "7.2000000000", "unit": "%"},
    )
    payload = question("What is my latest recorded value for hba1c?")
    result = build_question(PATIENT, payload, [old, latest], [old, newer, latest])
    assert not isinstance(result, QuestionStatus)
    claim = result.draft.claims[0]
    assert claim.fact_version_ids == (latest.version_id,)
    assert claim.proposed_content.fact.model_dump(mode="json")["value"] == "7.2000000000"
    assert claim.source_attribution.author_id == latest.author_id
    assert result == build_question(PATIENT, payload, [latest, old], [latest, newer, old])
    assert result.draft.unverified_text is None and not result.bindings


def test_empty_ambiguous_and_overflow_do_not_invent_answers():
    payload = question()
    assert build_question(PATIENT, payload, [], []).reason_code == "NO_MATCHING_RECORDS"
    rows = [fact(n) for n in range(10, 43)]
    assert build_question(PATIENT, payload, rows, rows).reason_code == "TOO_MANY_RECORDS"
    assert (
        build_question(
            PATIENT, question("What is my latest recorded hba1c?"), rows, rows
        ).reason_code
        == "LATEST_AMBIGUOUS"
    )


@pytest.mark.parametrize(
    "changes", [{"status": "PENDING"}, {"status": "REJECTED"}, {"patient_id": uuid4()}]
)
def test_unaccepted_or_foreign_state_fails_closed(changes):
    row = fact(**changes)
    with pytest.raises(HTTPException):
        build_question(PATIENT, question(), [row], [row])


async def test_endpoint_uses_unchanged_release_adapter_without_serializing_body(
    harness, monkeypatch
):
    from types import SimpleNamespace

    app = create_app()
    app.dependency_overrides[current_account] = lambda: SimpleNamespace(id=AUTHOR)
    app.dependency_overrides[patient_scope] = lambda: PATIENT
    app.dependency_overrides[release_session_factory] = lambda: harness["factory"]

    async def request_db():
        async with AsyncSession() as db:
            yield db

    app.dependency_overrides[get_db] = request_db
    prepare = AsyncMock(return_value=harness["request"])
    monkeypatch.setattr(endpoint, "prepare_question", prepare)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
        result = await api.post(
            f"/api/v1/patients/{PATIENT}/record-questions", json=question().model_dump(mode="json")
        )
    assert result.status_code == 200 and result.content == harness["body"]
    assert harness["trace"] == ["rollback", "commit", "rollback"]
    assert len(harness["sessions"]) == 3
    assert prepare.await_args.args[1:3] == (AUTHOR, PATIENT)


@pytest.mark.parametrize("kind", ["medication", "symptom", "history"])
def test_category_selection_includes_both_attributions_without_medication_adherence(kind):
    payloads = {
        "medication": {"kind": "medication", "medication_name": "synthetic recorded medication"},
        "symptom": {"kind": "symptom", "code": "fatigue", "description": "synthetic report"},
        "history": {"kind": "history", "category": "other", "description": "synthetic history"},
    }
    prompts = {
        "medication": "What medications are currently recorded?",
        "symptom": "What symptoms have been recorded?",
        "history": "What relevant history has been recorded?",
    }
    reported = fact(11, fact=payloads[kind])
    clinical = fact(
        12,
        fact=payloads[kind],
        author_role="practitioner",
        attribution="practitioner_managed",
        author_id=uuid4(),
    )
    unrelated = fact(13)
    rows = [reported, clinical, unrelated]
    before = [r.model_dump() for r in rows]
    result = build_question(PATIENT, question(prompts[kind]), rows, rows)
    assert not isinstance(result, QuestionStatus)
    assert {c.fact_version_ids[0] for c in result.draft.claims} == {
        reported.version_id,
        clinical.version_id,
    }
    assert [r.model_dump() for r in rows] == before


@pytest.mark.parametrize("change", ["revoke", "correct", "catalogue"])
async def test_database_change_after_lookup_withholds_response(
    database_bridge, monkeypatch, change
):
    from app.api import audited_response as adapter
    from app.services import response_release
    from tests.test_clinical_facts import entry, path

    env, _, row, _ = database_bridge
    original = adapter.prepare_release

    async def changed(*args):
        if change == "catalogue":
            monkeypatch.setattr(response_release, "catalogue_fingerprint", lambda: None)
        else:
            async with AsyncClient(
                transport=ASGITransport(app=env["app"]), base_url="http://test"
            ) as api:
                if change == "revoke":
                    response = await api.post(
                        f"/api/v1/patients/me/consents/{env['doctor_profile_id']}/revoke",
                        headers=env["auth"],
                    )
                else:
                    response = await api.post(
                        f"{path(env['patient_id'])}/{row.fact_id}/corrections",
                        headers=env["auth"],
                        json={
                            **entry("7.4"),
                            "expected_version_id": str(row.version_id),
                            "correction_reason": "synthetic competing correction",
                        },
                    )
                assert response.status_code in (200, 201)
        return await original(*args)

    monkeypatch.setattr(adapter, "prepare_release", changed)
    async with AsyncClient(transport=ASGITransport(app=env["app"]), base_url="http://test") as api:
        response = await api.post(
            f"/api/v1/patients/{env['patient_id']}/record-questions",
            headers=env["doctor_auth"],
            json=question().model_dump(mode="json"),
        )
    assert response.status_code in (403, 409, 503) and "records" not in response.text


@pytest.mark.parametrize("role", ["patient", "practitioner"])
async def test_database_release_and_no_replay(database_bridge, role):
    env, _, _, _ = database_bridge
    headers = env["auth"] if role == "patient" else env["doctor_auth"]
    body = question("What is my latest recorded hba1c?").model_dump(mode="json")
    async with AsyncClient(transport=ASGITransport(app=env["app"]), base_url="http://test") as api:
        path = f"/api/v1/patients/{env['patient_id']}/record-questions"
        result = await api.post(path, headers=headers, json=body)
        assert result.status_code == 200 and result.json()["records"]
        replay = await api.post(path, headers=headers, json=body)
        assert replay.status_code == 409 and "records" not in replay.text
        denied = await api.post(
            f"/api/v1/patients/{uuid4()}/record-questions", headers=headers, json=body
        )
        assert denied.status_code == 403 and "records" not in denied.text
