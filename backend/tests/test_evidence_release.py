"""Synthetic provenance is released only inside the existing exact-byte gate."""

import json
import secrets
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api import audited_response as adapter
from app.core.config import settings
from app.models import ResponseReleaseIntent
from app.schemas.evidence_aware_draft import EvidenceAwareDraftRequest
from app.schemas.record_questions import QuestionStatus, RecordQuestion
from app.services import evidence_provenance as projection
from app.services import response_release as release
from app.services.record_questions import build_question
from tests.database import migration
from tests.test_clinical_facts import race_environment as race_environment
from tests.test_consistency import PATIENT, fact
from tests.test_response_release import harness as harness
from tests.test_verification_gate import gated
from tests.test_verified_records_api import database_bridge as database_bridge


@pytest.fixture(autouse=True)
def signing_configuration(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", secrets.token_urlsafe(48))
    monkeypatch.setattr(settings, "ALGORITHM", "HS256")


async def test_v2_adapter_preserves_commit_order_and_passes_mode_to_both_transactions(harness):
    from unittest.mock import AsyncMock

    from tests.test_response_release import SCOPE, receive_forever

    gate = gated(harness["request"], [fact()])
    body = release.encode_records(gate, include_provenance=True)
    receipt = harness["receipt"].model_copy(
        update={
            "representation_version": "record-evidence-json-v2",
            "body_digest": release.body_digest(body),
        }
    )
    harness["prepare"].side_effect = None
    harness["prepare"].return_value = release._Prepared(receipt)
    harness["validate"].return_value = (body, "a" * 64)
    messages = []

    async def send(message):
        assert harness["trace"] == ["commit"]
        messages.append(message)

    response = adapter.AuditedRecordResponse(
        uuid4(), PATIENT, harness["request"], uuid4(), harness["factory"], include_provenance=True
    )
    await response(SCOPE, receive_forever, send)
    assert messages[1]["body"] == body and harness["trace"] == ["commit", "rollback"]
    assert harness["prepare"].await_args.kwargs == {"include_provenance": True}
    assert harness["validate"].await_args.kwargs == {"include_provenance": True}
    with pytest.raises(HTTPException):
        await response(SCOPE, receive_forever, AsyncMock())


def request_for(row, **changes):
    return build_question(
        row.patient_id,
        RecordQuestion(
            operation_id=uuid4(),
            question="What is my latest recorded hba1c?",
            include_synthetic_provenance=True,
            **changes,
        ),
        [row],
        [row],
    )


@pytest.mark.parametrize("role", ["patient", "practitioner"])
def test_v2_projection_and_v1_compatibility(role):
    row = fact(
        author_role=role,
        attribution="patient_reported" if role == "patient" else "practitioner_managed",
    )
    request = request_for(row)
    assert isinstance(request, EvidenceAwareDraftRequest)
    gate = gated(request, [row])
    v1 = release.encode_records(gate)
    assert set(json.loads(v1)) == {"records", "representation_version"}
    assert json.loads(v1)["representation_version"] == "record-response-json-v1"
    body = release.encode_records(gate, include_provenance=True)
    result = json.loads(body)
    assert result["representation_version"] == "record-evidence-json-v2"
    p = result["provenance"][0]
    assert p["fact_version_ids"] == [str(row.version_id)]
    assert p["source"]["label"] == "SYNTHETIC_FIXTURE_ONLY"
    assert p["evidence"]["unit_id"] == "synthetic-context-unit"
    assert p["rule_id"] == f"synthetic-{role}-lab-context"
    assert p["clinical_support_asserted"] is False
    assert body == release.encode_records(gate, include_provenance=True)
    changed = json.loads(body)
    changed["provenance"][0]["source"]["title"] = "forged clinical guidance"
    assert release.body_digest(json.dumps(changed).encode()) != release.body_digest(body)


@pytest.mark.parametrize("case", ["unbound", "hash", "manifest", "status", "fact", "catalogue"])
def test_projection_fails_closed(case, monkeypatch):
    row = fact()
    request = request_for(row)
    gate = gated(request, [row])
    claim = gate.claims[0]
    evidence = claim.evidence
    if case == "unbound":
        claim = claim.model_copy(update={"evidence": None})
    elif case == "catalogue":
        monkeypatch.setattr(projection, "catalogue_fingerprint", lambda: None)
    elif case == "hash":
        ref = evidence.binding.rule.evidence[0].model_copy(update={"unit_content_hash": "0" * 64})
        rule = evidence.binding.rule.model_copy(update={"evidence": (ref,)})
        binding = evidence.binding.model_copy(update={"rule": rule})
        claim = claim.model_copy(
            update={"evidence": evidence.model_copy(update={"binding": binding})}
        )
    elif case == "manifest":
        claim = claim.model_copy(
            update={"evidence": evidence.model_copy(update={"approval_manifest_hash": "0" * 64})}
        )
    else:
        app = evidence.applicability.model_copy(
            update={"status": "NOT_APPLICABLE"}
            if case == "status"
            else {"matched_fact_version_ids": (uuid4(),)}
        )
        claim = claim.model_copy(
            update={"evidence": evidence.model_copy(update={"applicability": app})}
        )
    with pytest.raises(HTTPException):
        release.encode_records(
            gate.model_copy(update={"claims": (claim,)}), include_provenance=True
        )


def test_missing_units_and_unsupported_questions_never_fall_back():
    row = fact(fact={"kind": "laboratory", "code": "hba1c", "value": "50", "unit": "mmol/mol"})
    assert request_for(row).reason_code == "SYNTHETIC_CONTEXT_UNSUPPORTED"
    result = build_question(
        PATIENT,
        RecordQuestion(
            operation_id=uuid4(),
            question="What medications are recorded?",
            include_synthetic_provenance=True,
        ),
        [],
        [],
    )
    assert (
        isinstance(result, QuestionStatus) and result.reason_code == "SYNTHETIC_CONTEXT_UNSUPPORTED"
    )


def test_additive_migration_and_orm_agree(monkeypatch):
    revision = migration("0008_evidence_release_representation")
    assert revision.down_revision == "0007_response_release_intent"
    assert len(revision.revision) <= 32  # Alembic's default version_num column.
    operations = Mock()
    monkeypatch.setattr(revision, "op", operations)
    revision.upgrade()
    clause = operations.create_check_constraint.call_args.args[2]
    assert "record-response-json-v1" in clause and "record-evidence-json-v2" in clause
    orm = next(
        c
        for c in ResponseReleaseIntent.__table__.constraints
        if c.name == "ck_release_representation"
    )
    assert str(orm.sqltext) == clause
    operations.execute.assert_not_called()  # No row rewrite, deletion or trigger changes.


@pytest.mark.parametrize("change", ["snapshot", "authorization", "body", "catalogue", "mode"])
async def test_v2_requires_exact_committed_binding(monkeypatch, change):
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.schemas.response_release import ReleaseIntent
    from app.schemas.verification_gate import GateRun
    from app.services.verification_audit import event_output, project_gate
    from tests.test_consistency import AUTHOR
    from tests.test_verification_audit import stored

    row = fact()
    request = request_for(row)
    gate = gated(request, [row])
    event = event_output(stored(project_gate(gate, AUTHOR, "patient")))
    body = release.encode_records(gate, include_provenance=True)
    receipt = ReleaseIntent(
        id=uuid4(),
        evaluation_event_id=event.id,
        release_policy_version="audited-record-release-v1",
        representation_version="record-response-json-v1"
        if change == "mode"
        else "record-evidence-json-v2",
        body_digest="0" * 64 if change == "body" else release.body_digest(body),
        recorded_at=datetime.now(UTC),
    )
    if change == "snapshot":
        reference = gate.verification.model_copy(
            update={
                "snapshot": gate.verification.snapshot.model_copy(
                    update={"history_version_ids": ()}
                )
            }
        )
        gate = gate.model_copy(update={"verification": reference})
    monkeypatch.setattr(
        release,
        "evaluate_patient_gate",
        AsyncMock(return_value=GateRun(evaluated_at=datetime.now(UTC), gate=gate)),
    )
    monkeypatch.setattr(release, "get_release_intent", AsyncMock(return_value=receipt))
    monkeypatch.setattr(release, "get_patient_audit_event", AsyncMock(return_value=event))
    monkeypatch.setattr(
        release,
        "authorize_patient_scope",
        AsyncMock(
            side_effect=HTTPException(403, "Patient scope denied")
            if change == "authorization"
            else None
        ),
    )
    if change == "catalogue":
        monkeypatch.setattr(release, "catalogue_fingerprint", lambda: None)
    async with AsyncSession() as db:
        with pytest.raises(HTTPException) as error:
            await release.revalidate_release(
                db, AUTHOR, PATIENT, request, release._Prepared(receipt), include_provenance=True
            )
    assert error.value.status_code == (
        403 if change == "authorization" else 503 if change == "catalogue" else 409
    )


@pytest.mark.parametrize("role", ["patient", "practitioner"])
async def test_database_v2_digest_replay_and_existing_v1(database_bridge, role):
    env, factory, _, _ = database_bridge
    auth = env["auth"] if role == "patient" else env["doctor_auth"]
    path = f"/api/v1/patients/{env['patient_id']}/record-questions"
    async with AsyncClient(transport=ASGITransport(app=env["app"]), base_url="http://test") as api:
        v1 = await api.post(
            path,
            headers=auth,
            json={"operation_id": str(uuid4()), "question": "What is my latest recorded hba1c?"},
        )
        assert (
            v1.status_code == 200
            and v1.json()["representation_version"] == "record-response-json-v1"
        )
        operation = uuid4()
        payload = {
            "operation_id": str(operation),
            "question": "What is my latest recorded hba1c?",
            "include_synthetic_provenance": True,
        }
        result = await api.post(path, headers=auth, json=payload)
        assert result.status_code == 200 and result.json()["provenance"]
        async with factory() as db:
            intents = list(await db.scalars(select(ResponseReleaseIntent)))
            assert {r.representation_version for r in intents} == {
                "record-response-json-v1",
                "record-evidence-json-v2",
            }
            assert any(
                r.representation_version == "record-evidence-json-v2"
                and r.body_digest == release.body_digest(result.content)
                for r in intents
            )
        replay = await api.post(path, headers=auth, json=payload)
        assert replay.status_code == 409 and "provenance" not in replay.text


@pytest.mark.parametrize("change", ["revoke", "correct", "catalogue"])
async def test_database_v2_revalidation_withholds(database_bridge, monkeypatch, change):
    from tests.test_clinical_facts import entry, path

    env, _, row, _ = database_bridge
    original = adapter.revalidate_release

    async def changed(*args, **kwargs):
        if change == "catalogue":
            monkeypatch.setattr(projection, "catalogue_fingerprint", lambda: None)
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
        return await original(*args, **kwargs)

    monkeypatch.setattr(adapter, "revalidate_release", changed)
    async with AsyncClient(transport=ASGITransport(app=env["app"]), base_url="http://test") as api:
        result = await api.post(
            f"/api/v1/patients/{env['patient_id']}/record-questions",
            headers=env["doctor_auth"],
            json={
                "operation_id": str(uuid4()),
                "question": "What is my latest recorded hba1c?",
                "include_synthetic_provenance": True,
            },
        )
    assert (
        result.status_code in (403, 409, 503)
        and "records" not in result.text
        and "provenance" not in result.text
    )
