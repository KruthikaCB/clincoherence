"""Focused offline Ollama adapter, source-grounding and confirmation-boundary tests."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.models import (
    ClinicalFactVersion,
    ConfirmationDecision,
    ConfirmationProposal,
    ExtractionMessage,
)
from app.services import confirmations
from app.services import ollama_extraction as ollama
from app.services.extraction_provider import (
    extraction_provider,
    validate_candidates,
)

TIME = "2026-01-15T10:00:00Z"
REAL_CLIENT = httpx.AsyncClient
NOTE = f"The patient's HbA1c was 7.1 % at {TIME}."
LAB = {"kind": "laboratory", "code": "hba1c", "value": "7.1", "unit": "%"}


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setattr(settings, "LLM_MODEL_NAME", "qwen3:0.6b")


def install_transport(monkeypatch, handler):
    original = REAL_CLIENT

    def factory(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(ollama.httpx, "AsyncClient", factory)


def reply(fact=LAB, quote=NOTE, **changes):
    result = {
        "model": "qwen3:0.6b",
        "done": True,
        "done_reason": "stop",
        "message": {
            "role": "assistant",
            "content": json.dumps(
                {"candidates": [{"fact": fact, "observed_at": TIME, "source_quote": quote}]}
            ),
        },
    }
    result.update(changes)
    return httpx.Response(200, json=result)


async def test_cpu_schema_and_exact_source_proposal(monkeypatch):
    def handler(request):
        assert str(request.url) == "http://127.0.0.1:11434/api/chat"
        payload = json.loads(request.content)
        assert payload["model"] == "qwen3:0.6b"
        assert payload["stream"] is False and payload["think"] is False
        assert payload["options"]["num_gpu"] == 0
        assert payload["options"]["temperature"] == 0
        assert payload["messages"][1] == {"role": "user", "content": NOTE}
        assert payload["format"]["additionalProperties"] is False
        assert "patient_id" not in payload and "Authorization" not in request.headers
        return reply()

    install_transport(monkeypatch, handler)
    provider = extraction_provider()
    raw = await provider.extract(NOTE)
    proposals = validate_candidates(raw, NOTE, grounding_version=ollama.NATURAL_GROUNDING_VERSION)
    assert len(proposals) == 1 and str(proposals[0].fact.value) == "7.1"
    assert proposals[0].source_quote == NOTE
    with pytest.raises(ValueError):
        validate_candidates(raw, NOTE)  # Old mock grounding is not silently weakened.


@pytest.mark.parametrize(
    "origin",
    [
        "http://remote.invalid:11434",
        "https://127.0.0.1:11434",
        "http://127.0.0.1:8000",
        "http://user:secret@127.0.0.1:11434",
        "http://127.0.0.1:11434?host=remote",
        "http://127.0.0.1:11434/api/chat",
    ],
)
def test_local_origin_only(monkeypatch, origin):
    monkeypatch.setattr(settings, "LLM_BASE_URL", origin)
    with pytest.raises(HTTPException) as error:
        extraction_provider()
    assert error.value.status_code == 503


def test_fixed_model_only(monkeypatch):
    monkeypatch.setattr(settings, "LLM_MODEL_NAME", "other-model")
    with pytest.raises(HTTPException):
        extraction_provider()


@pytest.mark.parametrize(
    "envelope",
    [
        {"done": False},
        {"done_reason": "length"},
        {"model": "other-model"},
        {"message": {"role": "assistant", "content": "not json"}},
        {"message": {"role": "assistant", "content": '{"candidates": [], "approved": true}'}},
        {
            "message": {
                "role": "assistant",
                "content": '{"candidates": [{"fact": {"kind":"unknown"}}]}',
            }
        },
    ],
)
async def test_invalid_output_fails_closed(monkeypatch, envelope):
    install_transport(monkeypatch, lambda request: reply(**envelope))
    with pytest.raises(ollama.OllamaOutputError):
        await extraction_provider().extract(NOTE)


@pytest.mark.parametrize(
    "fact,quote",
    [
        ({**LAB, "value": "9.9"}, NOTE),
        ({**LAB, "unit": "mmol/mol"}, NOTE),
        (LAB, NOTE.replace("was", "was not")),
        (LAB, f"Consider HbA1c 7.1 % at {TIME}."),
        (LAB, NOTE + " Prior observation at 2025-01-01T10:00:00Z."),
        (
            {"kind": "diagnosis", "code": "type_2_diabetes"},
            f"Family has type 2 diabetes at {TIME}.",
        ),
        (
            {
                "kind": "medication",
                "medication_name": "metformin",
                "dose_value": "1000",
                "dose_unit": "mg",
            },
            f"Patient takes metformin 500 mg at {TIME}.",
        ),
        ({"kind": "medication", "medication_name": "metformin"}, f"Increase metformin at {TIME}."),
    ],
)
async def test_ungrounded_or_treatment_proposals_rejected(monkeypatch, fact, quote):
    install_transport(monkeypatch, lambda request: reply(fact, quote))
    with pytest.raises(ollama.OllamaOutputError):
        await extraction_provider().extract(quote)


@pytest.mark.parametrize(
    "fact,quote",
    [
        (
            {"kind": "diagnosis", "code": "type_2_diabetes"},
            f"Patient has type 2 diabetes at {TIME}.",
        ),
        (
            {
                "kind": "medication",
                "medication_name": "metformin",
                "dose_value": "500",
                "dose_unit": "mg",
                "frequency": "twice daily",
            },
            f"Patient takes metformin 500 mg twice daily at {TIME}.",
        ),
        (
            {
                "kind": "symptom",
                "code": "fatigue",
                "description": f"Patient reports fatigue at {TIME}.",
            },
            f"Patient reports fatigue at {TIME}.",
        ),
        (
            {
                "kind": "history",
                "category": "family_history",
                "description": f"Family history of diabetes at {TIME}.",
            },
            f"Family history of diabetes at {TIME}.",
        ),
    ],
)
async def test_supported_recorded_fact_kinds(monkeypatch, fact, quote):
    install_transport(monkeypatch, lambda request: reply(fact, quote))
    raw = await extraction_provider().extract(quote)
    assert (
        len(validate_candidates(raw, quote, grounding_version=ollama.NATURAL_GROUNDING_VERSION))
        == 1
    )


async def test_no_invented_observation_time(monkeypatch):
    def handler(request):
        pytest.fail("Missing timestamp must fail before any network request")

    install_transport(monkeypatch, handler)
    with pytest.raises(ollama.MissingObservationTimeError):
        await extraction_provider().extract("Patient takes metformin 500 mg.")


@pytest.mark.parametrize(
    "failure,expected",
    [
        (httpx.ConnectError("PRIVATE_NOTE"), ollama.OllamaUnavailableError),
        (httpx.ReadTimeout("PRIVATE_NOTE"), TimeoutError),
    ],
)
async def test_network_failures_sanitized(monkeypatch, failure, expected):
    def handler(request):
        raise failure

    install_transport(monkeypatch, handler)
    with pytest.raises(expected) as error:
        await extraction_provider().extract(NOTE)
    assert "PRIVATE_NOTE" not in str(error.value)


async def test_no_redirects_or_model_downloads(monkeypatch):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(307, headers={"location": "https://remote.invalid"})

    install_transport(monkeypatch, handler)
    with pytest.raises(ollama.OllamaUnavailableError):
        await extraction_provider().extract(NOTE)
    assert calls == ["http://127.0.0.1:11434/api/chat"]


async def test_duplicate_proposals_and_ambiguous_quotes(monkeypatch):
    install_transport(monkeypatch, lambda request: reply())
    raw = await extraction_provider().extract(NOTE)
    with pytest.raises(ValueError):
        validate_candidates(raw + raw, NOTE, grounding_version=ollama.NATURAL_GROUNDING_VERSION)
    with pytest.raises(ollama.OllamaOutputError):
        await extraction_provider().extract(NOTE + " " + NOTE)


@pytest.mark.parametrize(
    "failure,status",
    [
        (ollama.OllamaUnavailableError(), 503),
        (TimeoutError(), 504),
        (ollama.MissingObservationTimeError(), 422),
        (ollama.OllamaOutputError(), 502),
    ],
)
async def test_failed_extraction_never_persists(monkeypatch, failure, status):
    from app.schemas.extraction import ExtractMessage

    monkeypatch.setattr(confirmations, "authorize_patient_scope", AsyncMock())
    db = MagicMock()
    db.scalar = AsyncMock(return_value=None)
    db.commit = AsyncMock()
    provider = MagicMock(name="provider")
    provider.name, provider.version = "ollama", "qwen3-0.6b-cpu-v1"
    provider.grounding_version = ollama.NATURAL_GROUNDING_VERSION
    provider.timeout_seconds = 5
    provider.extract = AsyncMock(side_effect=failure)
    with pytest.raises(HTTPException) as error:
        await confirmations.extract_message(
            db,
            uuid4(),
            uuid4(),
            ExtractMessage(source_message_id=uuid4(), text=NOTE, data_origin="synthetic"),
            provider,
        )
    assert error.value.status_code == status
    db.add.assert_not_called()


async def test_success_stores_pending_proposals_only(monkeypatch):
    from app.schemas.extraction import ExtractMessage

    actor, patient = uuid4(), uuid4()
    monkeypatch.setattr(confirmations, "authorize_patient_scope", AsyncMock())
    monkeypatch.setattr(
        confirmations,
        "lock_patient_scope",
        AsyncMock(return_value=SimpleNamespace(id=actor, role="practitioner")),
    )
    db = MagicMock()
    db.scalar = AsyncMock(return_value=None)
    db.commit = AsyncMock()

    def flush():
        for call in db.add.call_args_list:
            row = call.args[0]
            if row.id is None:
                row.id = uuid4()
            if isinstance(row, ConfirmationProposal):
                row.created_at = datetime.now(UTC)

    db.flush = AsyncMock(side_effect=flush)
    install_transport(monkeypatch, lambda request: reply())
    result = await confirmations.extract_message(
        db,
        actor,
        patient,
        ExtractMessage(source_message_id=uuid4(), text=NOTE, data_origin="synthetic"),
        extraction_provider(),
    )
    assert len(result.confirmations) == 1
    assert result.confirmations[0].status == "PENDING"
    assert result.confirmations[0].speaker_id == actor
    assert result.confirmations[0].patient_id == patient
    assert result.confirmations[0].grounding_version == ollama.NATURAL_GROUNDING_VERSION
    assert all(
        isinstance(call.args[0], (ExtractionMessage, ConfirmationProposal))
        for call in db.add.call_args_list
    )


@pytest.mark.parametrize("confirm", [False, True])
async def test_original_confirmation_path_revalidates_ollama_proposals(monkeypatch, confirm):
    actor, patient, message_id, proposal_id = uuid4(), uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    source = ExtractionMessage(
        id=message_id,
        patient_id=patient,
        speaker_id=actor,
        speaker_role="practitioner",
        text=NOTE,
        data_origin="synthetic",
        source_sha256=confirmations.sha256(NOTE),
        provider_name="ollama",
        provider_version="qwen3-0.6b-cpu-v1",
        grounding_version=ollama.NATURAL_GROUNDING_VERSION,
    )
    proposal = ConfirmationProposal(
        id=proposal_id,
        message_id=message_id,
        patient_id=patient,
        speaker_id=actor,
        speaker_role="practitioner",
        ordinal=0,
        payload=LAB,
        observed_at=datetime.fromisoformat(TIME.replace("Z", "+00:00")),
        source_start=0,
        source_end=len(NOTE),
        source_quote=NOTE,
        created_at=now,
    )
    monkeypatch.setattr(
        confirmations,
        "lock_patient_scope",
        AsyncMock(return_value=SimpleNamespace(id=actor, role="practitioner")),
    )
    monkeypatch.setattr(confirmations, "authorize_patient_scope", AsyncMock())
    monkeypatch.setattr(confirmations, "clock", AsyncMock(return_value=now))
    db = MagicMock()
    db.scalar = AsyncMock(return_value=proposal)
    db.get = AsyncMock(side_effect=[None, source])
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    result = await confirmations.decide(db, actor, patient, proposal_id, confirm=confirm)
    assert result.status == ("CONFIRMED" if confirm else "REJECTED")
    writes = [call.args[0] for call in db.add.call_args_list]
    assert len([row for row in writes if isinstance(row, ClinicalFactVersion)]) == int(confirm)
    assert len([row for row in writes if isinstance(row, ConfirmationDecision)]) == 1


async def test_unavailable_or_oversized_body(monkeypatch):
    for response, expected in [
        (httpx.Response(404, json={"error": "PRIVATE_NOTE"}), ollama.OllamaUnavailableError),
        (httpx.Response(200, content=b"x" * 131073), ollama.OllamaOutputError),
    ]:
        install_transport(monkeypatch, lambda request, response=response: response)
        with pytest.raises(expected):
            await extraction_provider().extract(NOTE)


@pytest.mark.parametrize("unit", ollama.EGFR_UNITS)
async def test_egfr_notation_is_canonical_without_changing_source_or_value(monkeypatch, unit):
    note = f"The patient's eGFR was 58 {unit} on {TIME}."
    payload = {"kind": "laboratory", "code": "egfr", "value": "58", "unit": unit}
    install_transport(monkeypatch, lambda request: reply(payload, note))
    raw = await extraction_provider().extract(note)
    candidates = validate_candidates(raw, note, grounding_version=ollama.NATURAL_GROUNDING_VERSION)
    assert len(candidates) == 1
    assert str(candidates[0].fact.value) == "58"
    assert candidates[0].fact.unit == "mL/min/1.73m2"
    assert candidates[0].source_quote == note
    assert note[candidates[0].source_start : candidates[0].source_end] == note
    # Confirmation reload uses the same strict grounding with the stored canonical fact.
    assert (
        validate_candidates(raw, note, grounding_version=ollama.NATURAL_GROUNDING_VERSION)
        == candidates
    )


@pytest.mark.parametrize(
    "unit,value,code",
    [
        ("mL/min", "58", "egfr"),
        ("L/min/1.73m2", "0.058", "egfr"),
        ("mL/min/1.73 m\u00b2", "59", "egfr"),
        ("mL/min/1.73 m\u00b2", "58", "creatinine"),
    ],
)
async def test_egfr_formatting_does_not_permit_conversion_or_mismatched_facts(
    monkeypatch, unit, value, code
):
    note = f"The patient's eGFR was 58 mL/min/1.73 m\u00b2 on {TIME}."
    payload = {"kind": "laboratory", "code": code, "value": value, "unit": unit}
    install_transport(monkeypatch, lambda request: reply(payload, note))
    with pytest.raises(ollama.OllamaOutputError):
        await extraction_provider().extract(note)
