"""Summary projection binding and unchanged audited transport; synthetic fixtures."""

import json
import secrets
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import audited_response as adapter
from app.core.config import settings
from app.models import ResponseReleaseIntent, User
from app.schemas.clinical_summary import (
    ClinicalSummaryReleaseRequest,
    ClinicalSummaryResponse,
    ClinicalSummaryRun,
)
from app.schemas.response_release import RELEASE_POLICY, SUMMARY_REPRESENTATION, ReleaseIntent
from app.schemas.verification_gate import GateRun
from app.services import clinical_summary_release as service
from app.services.clinical_summary import build_clinical_summary
from app.services.response_release import _Prepared, body_digest
from app.services.verification_audit import event_output, project_gate
from tests.database import migration
from tests.test_auth import add_practitioner, headers
from tests.test_clinical_facts import entry, path
from tests.test_clinical_summary import lab
from tests.test_consents import grant, revoke
from tests.test_consistency import AUTHOR, PATIENT
from tests.test_response_release import SCOPE, receive_forever
from tests.test_response_release import harness as harness
from tests.test_verification_audit import setup_patient_fact, stored
from tests.test_verification_gate import gated


@pytest.fixture(autouse=True)
def signing_configuration(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", secrets.token_urlsafe(48))


@pytest.fixture
def evaluation(monkeypatch):
    state = {"rows": [lab(10, "8.2", -1), lab(11, "7.1")]}

    async def summary(_db, _actor, patient):
        return ClinicalSummaryRun(
            evaluated_at=datetime.now(UTC),
            summary=build_clinical_summary(patient, state["rows"], state["rows"]),
        )

    async def gate(_db, _actor, _patient, request):
        return GateRun(evaluated_at=datetime.now(UTC), gate=gated(request, state["rows"]))

    monkeypatch.setattr(service, "summarize_patient_records", AsyncMock(side_effect=summary))
    monkeypatch.setattr(service, "evaluate_patient_gate", AsyncMock(side_effect=gate))
    return state


async def prepared_context(monkeypatch):
    request = ClinicalSummaryReleaseRequest(patient_id=PATIENT)
    operation = uuid4()
    draft, gate, body = await service._evaluate(AsyncMock(), AUTHOR, PATIENT, request, operation)
    event = event_output(stored(project_gate(gate, AUTHOR, "patient"), operation_id=operation))
    intent = ReleaseIntent(
        id=uuid4(),
        evaluation_event_id=event.id,
        release_policy_version=RELEASE_POLICY,
        representation_version=SUMMARY_REPRESENTATION,
        body_digest=body_digest(body),
        recorded_at=datetime.now(UTC),
    )
    monkeypatch.setattr(service, "get_release_intent", AsyncMock(return_value=intent))
    monkeypatch.setattr(service, "get_patient_audit_event", AsyncMock(return_value=event))
    monkeypatch.setattr(service, "authorize_patient_scope", AsyncMock())
    return request, operation, draft, event, intent, body


async def test_representation_is_deterministic_snapshot_bound_and_not_clinical(evaluation):
    request = ClinicalSummaryReleaseRequest(patient_id=PATIENT)
    operation = uuid4()
    draft, gate, body = await service._evaluate(AsyncMock(), AUTHOR, PATIENT, request, operation)
    again = await service._evaluate(AsyncMock(), AUTHOR, PATIENT, request, operation)
    assert body == again[2]
    decoded = json.loads(body)
    assert decoded["representation_version"] == SUMMARY_REPRESENTATION
    assert decoded["summary"]["patient_id"] == str(PATIENT)
    assert decoded["summary"]["hba1c_change"]["direction"] == "DECREASED"
    assert decoded["summary"]["hba1c_change"]["comparison"]["numeric_difference"] == "-1.1"
    assert decoded["summary"]["missing_information"] == ["egfr", "medications"]
    assert decoded["summary"]["snapshot"] == gate.verification.snapshot.model_dump(mode="json")
    assert "NOT_CLINICAL_CORRECTNESS" in decoded["verification_scope"]
    assert all(c.claim_type == "FACT_RESTATEMENT" for c in draft.draft.claims)
    assert not gate.http_release_authorized
    metadata = project_gate(gate, AUTHOR, "patient").model_dump_json()
    assert "synthetic fixture" not in metadata and "rendered_text" not in metadata


async def test_conflicts_are_released_as_ambiguity_not_arbitrary_values(evaluation):
    evaluation["rows"] = [lab(10, "7.1"), lab(11, "8.2")]
    _, _, body = await service._evaluate(
        AsyncMock(), AUTHOR, PATIENT, ClinicalSummaryReleaseRequest(patient_id=PATIENT), uuid4()
    )
    point = json.loads(body)["summary"]["latest_hba1c"]
    assert point["status"] == "AMBIGUOUS" and point["value"] is None
    assert len(point["records"]) == 2


@pytest.mark.parametrize("count", [0, 33])
async def test_unchanged_gate_limits_withhold_without_truncation(evaluation, count):
    evaluation["rows"] = [lab(100 + i, day=-i) for i in range(count)]
    with pytest.raises(HTTPException) as error:
        await service._evaluate(
            AsyncMock(), AUTHOR, PATIENT, ClinicalSummaryReleaseRequest(patient_id=PATIENT), uuid4()
        )
    assert error.value.status_code == 409


@pytest.mark.parametrize("extra", ["summary", "PASS", "body_digest", "intent_id"])
def test_requests_cannot_supply_results_or_release_authority(extra):
    with pytest.raises(ValidationError):
        ClinicalSummaryReleaseRequest.model_validate({"patient_id": PATIENT, extra: "forged"})


def test_unsupported_representation_rejected_before_preparation(harness):
    with pytest.raises(HTTPException) as error:
        adapter.AuditedRecordResponse(
            AUTHOR,
            PATIENT,
            {"patient_id": PATIENT, "representation_version": "unknown"},
            uuid4(),
            harness["factory"],
            clinical_summary=True,
        )
    assert error.value.status_code == 422
    assert harness["sessions"] == []
    with pytest.raises(ValueError):
        adapter.AuditedRecordResponse(
            AUTHOR,
            PATIENT,
            {},
            uuid4(),
            harness["factory"],
            clinical_summary=True,
            include_provenance=True,
        )


async def test_patient_mismatch_fails_before_summary_lookup(evaluation):
    with pytest.raises(HTTPException) as error:
        await service._evaluate(
            AsyncMock(), AUTHOR, PATIENT, ClinicalSummaryReleaseRequest(patient_id=uuid4()), uuid4()
        )
    assert error.value.status_code == 403
    service.summarize_patient_records.assert_not_awaited()


async def test_revalidation_rebuilds_exact_committed_bytes(monkeypatch, evaluation):
    request, operation, _, _, intent, body = await prepared_context(monkeypatch)
    result, _ = await service.revalidate_summary_release(
        AsyncMock(), AUTHOR, PATIENT, request, operation, _Prepared(intent)
    )
    assert result == body and body_digest(result) == intent.body_digest


@pytest.mark.parametrize(
    "fault", ["body", "mode", "operation", "facts", "snapshot", "revoked", "catalogue"]
)
async def test_tampering_and_stale_context_withhold(monkeypatch, evaluation, fault):
    request, operation, _, _, intent, _ = await prepared_context(monkeypatch)
    if fault in {"body", "mode"}:
        intent = intent.model_copy(
            update={
                "body_digest": "0" * 64,
                **(
                    {"representation_version": "record-response-json-v1"} if fault == "mode" else {}
                ),
            }
        )
        service.get_release_intent.return_value = intent
    if fault == "operation":
        operation = uuid4()
    if fault == "facts":
        evaluation["rows"] = [lab(10, "8.2", -1), lab(11, "6.9")]
    if fault == "snapshot":
        evaluation["rows"].append(lab(12, "7.1", -2))
    if fault == "revoked":
        service.authorize_patient_scope.side_effect = HTTPException(403, "denied")
    if fault == "catalogue":
        monkeypatch.setattr(service, "catalogue_fingerprint", lambda: "0" * 64)
    with pytest.raises(HTTPException):
        await service.revalidate_summary_release(
            AsyncMock(), AUTHOR, PATIENT, request, operation, _Prepared(intent)
        )


async def test_preparation_flushes_metadata_intent_but_never_commits(monkeypatch, evaluation):
    request, operation, _, event, _, body = await prepared_context(monkeypatch)
    monkeypatch.setattr(service, "record_patient_gate_evaluation", AsyncMock(return_value=event))
    db = MagicMock(spec=AsyncSession)
    db.scalar = AsyncMock(return_value=None)
    db.commit = AsyncMock()

    async def flush():
        row = db.add.call_args.args[0]
        row.id, row.recorded_at = uuid4(), datetime.now(UTC)

    db.flush = AsyncMock(side_effect=flush)
    result = await service.prepare_summary_release(db, AUTHOR, PATIENT, request, operation)
    assert result.intent.body_digest == body_digest(body)
    assert result.intent.representation_version == SUMMARY_REPRESENTATION
    db.commit.assert_not_awaited()
    db.scalar.return_value = uuid4()
    with pytest.raises(HTTPException) as error:
        await service.prepare_summary_release(db, AUTHOR, PATIENT, request, operation)
    assert error.value.status_code == 409


@pytest.mark.parametrize("failure", [None, "commit", "prepare", "revoked", "stale"])
async def test_adapter_preserves_commit_before_send_and_withholding(monkeypatch, harness, failure):
    body = (
        ClinicalSummaryResponse(summary=build_clinical_summary(PATIENT, [lab()], [lab()]))
        .model_dump_json()
        .encode()
    )
    receipt = harness["receipt"].model_copy(
        update={
            "representation_version": SUMMARY_REPRESENTATION,
            "body_digest": body_digest(body),
        }
    )
    preparing = AsyncMock(return_value=_Prepared(receipt))
    validating = AsyncMock(return_value=(body, "a" * 64))
    if failure == "prepare":
        preparing.side_effect = HTTPException(503, "private")
    if failure in {"revoked", "stale"}:
        validating.side_effect = HTTPException(403 if failure == "revoked" else 409, "private")
    monkeypatch.setattr(adapter, "prepare_summary_release", preparing)
    monkeypatch.setattr(adapter, "revalidate_summary_release", validating)
    factory = harness["factory"]

    def owned():
        db = factory()
        if failure == "commit":
            db.commit.side_effect = RuntimeError("private")
        return db

    messages = []

    async def send(message):
        if message.get("status") == 200 or message.get("body") == body:
            assert harness["trace"] == ["commit"]
        messages.append(message)

    response = adapter.AuditedRecordResponse(
        AUTHOR,
        PATIENT,
        ClinicalSummaryReleaseRequest(patient_id=PATIENT),
        uuid4(),
        owned,
        clinical_summary=True,
    )
    await response(SCOPE, receive_forever, send)
    if failure:
        assert messages[0]["status"] != 200 and messages[-1]["body"] != body
        assert b"private" not in messages[-1]["body"]
    else:
        assert messages[1]["body"] == body and harness["trace"] == ["commit", "rollback"]
        assert len(harness["sessions"]) == 2
        with pytest.raises(HTTPException):
            await response(SCOPE, receive_forever, send)
    harness["prepare"].assert_not_awaited()  # No accidental record-v1 path.


def test_additive_migration_matches_orm_without_rewriting_or_removing_triggers(monkeypatch):
    revision = migration("0010_clinical_summary_release")
    assert revision.down_revision == "0009_demo_patients" and len(revision.revision) <= 32
    operations = Mock()
    monkeypatch.setattr(revision, "op", operations)
    revision.upgrade()
    operations.execute.assert_not_called()
    clause = operations.create_check_constraint.call_args.args[2]
    orm = next(
        c
        for c in ResponseReleaseIntent.__table__.constraints
        if c.name == "ck_release_representation"
    )
    assert str(orm.sqltext) == clause
    assert all(
        r in clause
        for r in (SUMMARY_REPRESENTATION, "record-response-json-v1", "record-evidence-json-v2")
    )
    revision.downgrade()
    assert "Cannot downgrade with clinical summary release history" in str(
        operations.execute.call_args.args[0]
    )


async def test_postgresql_summary_durability_and_replay_denial(db, api):
    actor, patient, _, _, _ = await setup_patient_fact(db, api)
    request, operation = ClinicalSummaryReleaseRequest(patient_id=patient), uuid4()
    prepared = await service.prepare_summary_release(db, actor, patient, request, operation)
    await db.commit()
    body, _ = await service.revalidate_summary_release(
        db, actor, patient, request, operation, prepared
    )
    assert body_digest(body) == prepared.intent.body_digest
    with pytest.raises(HTTPException):
        await service.prepare_summary_release(db, actor, patient, request, operation)
    await db.rollback()


async def test_postgresql_correction_after_commit_withholds_summary(db, api):
    actor, patient, _, _, row = await setup_patient_fact(db, api)
    request, operation = ClinicalSummaryReleaseRequest(patient_id=patient), uuid4()
    prepared = await service.prepare_summary_release(db, actor, patient, request, operation)
    await db.commit()
    user = await db.get(User, actor)
    payload = {
        **entry("8.2"),
        "expected_version_id": str(row.version_id),
        "correction_reason": "Synthetic correction",
    }
    response = await api.post(
        f"{path(patient)}/{row.fact_id}/corrections", headers=headers(user), json=payload
    )
    assert response.status_code == 201
    with pytest.raises(HTTPException):
        await service.revalidate_summary_release(db, actor, patient, request, operation, prepared)
    await db.rollback()


async def test_postgresql_revocation_after_commit_withholds_summary(db, api):
    actor, patient_id, _, _, _ = await setup_patient_fact(db, api)
    patient = await db.get(User, actor)
    doctor, profile = await add_practitioner(db)
    doctor_id, profile_id = doctor.id, profile.id
    assert (await grant(api, patient, profile_id)).status_code == 200
    request, operation = ClinicalSummaryReleaseRequest(patient_id=patient_id), uuid4()
    prepared = await service.prepare_summary_release(db, doctor_id, patient_id, request, operation)
    await db.commit()
    await db.refresh(patient)
    assert (await revoke(api, patient, profile_id)).status_code == 200
    with pytest.raises(HTTPException) as error:
        await service.revalidate_summary_release(
            db, doctor_id, patient_id, request, operation, prepared
        )
    assert error.value.status_code == 403
