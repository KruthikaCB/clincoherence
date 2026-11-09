"""Durable preparation before exact-byte ASGI handoff; no delivery assertion."""

import asyncio
import hashlib
import json
import secrets
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import audited_response as adapter
from app.core.config import settings
from app.core.security import decode_access_token
from app.models import ResponseReleaseIntent, User, VerificationAuditEvent
from app.schemas.response_release import RELEASE_POLICY, REPRESENTATION, ReleaseIntent
from app.services import response_release as service
from app.services.consistency import version_input
from tests.database import migration
from tests.test_auth import add_practitioner
from tests.test_clinical_facts import entry, path, wait_until_blocked
from tests.test_clinical_facts import race_environment as race_environment
from tests.test_consents import grant, revoke
from tests.test_consistency import AUTHOR, PATIENT, fact
from tests.test_draft_verification import claim_for
from tests.test_evidence_aware_draft import composed
from tests.test_verification_audit import setup_patient_fact
from tests.test_verification_gate import gated

SCOPE = {"type": "http", "asgi": {"version": "3.0"}, "method": "POST", "path": "/test-only"}


@pytest.fixture(autouse=True)
def signing_configuration(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", secrets.token_urlsafe(48))


async def receive_forever():
    await asyncio.Event().wait()


def test_exact_versioned_encoding_is_deterministic_and_no_framework_body():
    row = fact()
    gate = gated(composed(claim_for(row)), [row])
    body = service.encode_records(gate)
    assert body == service.encode_records(gate)
    assert service.body_digest(body) == hashlib.sha256(body).hexdigest()
    assert json.loads(body)["representation_version"] == REPRESENTATION
    assert set(json.loads(body)) == {"representation_version", "records"}
    assert json.loads(body)["records"][0]["text"] == gate.rendered_records[0].text
    assert service.body_digest(body + b" ") != service.body_digest(body)


@pytest.mark.parametrize("missing", [True, False])
def test_encoder_never_encodes_withheld_gate(missing):
    row = fact()
    request = composed(claim_for(row, proposed_content=None if missing else "synthetic prose"))
    with pytest.raises(HTTPException):
        service.encode_records(gated(request, [row]))


def test_migration_dependency_and_metadata_minimization():
    assert migration("0007_response_release_intent").down_revision == "0006_verification_audit"
    assert set(ResponseReleaseIntent.__table__.columns.keys()) == {
        "id",
        "evaluation_event_id",
        "release_policy_version",
        "representation_version",
        "body_digest",
        "recorded_at",
    }


@pytest.fixture
def harness(monkeypatch):
    row = fact()
    request = composed(claim_for(row))
    body = service.encode_records(gated(request, [row]))
    receipt = ReleaseIntent(
        id=uuid4(),
        evaluation_event_id=uuid4(),
        release_policy_version=RELEASE_POLICY,
        representation_version=REPRESENTATION,
        body_digest=service.body_digest(body),
        recorded_at=datetime.now(UTC),
    )
    sessions = []
    trace = []

    def factory():
        db = MagicMock(spec=AsyncSession)
        db.is_active = True
        db.in_transaction.return_value = False
        db.new, db.dirty, db.deleted = [], [], []
        db.__aenter__ = AsyncMock(return_value=db)
        db.__aexit__ = AsyncMock(return_value=None)
        db.commit = AsyncMock(side_effect=lambda: trace.append("commit"))
        db.rollback = AsyncMock(side_effect=lambda: trace.append("rollback"))
        sessions.append(db)
        return db

    async def prepare(*args):
        await asyncio.sleep(0)
        return service._Prepared(receipt)

    preparing = AsyncMock(side_effect=prepare)
    validating = AsyncMock(return_value=(body, "a" * 64))
    identity = AsyncMock(return_value=(1, 1))
    monkeypatch.setattr(adapter, "prepare_release", preparing)
    monkeypatch.setattr(adapter, "revalidate_release", validating)
    monkeypatch.setattr(adapter, "transaction_identity", identity)
    monkeypatch.setattr(adapter, "catalogue_fingerprint", lambda: "a" * 64)

    def response(**options):
        return adapter.AuditedRecordResponse(AUTHOR, PATIENT, request, uuid4(), factory, **options)

    return dict(
        body=body,
        request=request,
        receipt=receipt,
        sessions=sessions,
        trace=trace,
        prepare=preparing,
        validate=validating,
        identity=identity,
        factory=factory,
        response=response,
    )


async def test_precommit_failure_sends_no_protected_headers_or_body(harness):
    h = harness
    factory = h["factory"]

    def failing_factory():
        db = factory()
        db.commit.side_effect = SQLAlchemyError("SYNTHETIC_PRIVATE_DETAIL")
        return db

    response = adapter.AuditedRecordResponse(
        AUTHOR, PATIENT, h["request"], uuid4(), failing_factory
    )
    messages = []
    await response(SCOPE, receive_forever, AsyncMock(side_effect=messages.append))
    assert messages[0]["status"] == 503
    assert h["body"] not in [m.get("body") for m in messages]
    assert b"PRIVATE_DETAIL" not in b"".join(m.get("body", b"") for m in messages)
    h["validate"].assert_not_awaited()
    assert len(h["sessions"]) == 1


@pytest.mark.parametrize("status", [401, 403, 409, 422, 503])
async def test_prepare_failures_never_reach_handoff(harness, status):
    h = harness
    h["prepare"].side_effect = HTTPException(status, "SYNTHETIC_PRIVATE_DETAIL")
    messages = []
    await h["response"]()(SCOPE, receive_forever, AsyncMock(side_effect=messages.append))
    assert messages[0]["status"] == status
    h["sessions"][0].commit.assert_not_awaited()
    h["validate"].assert_not_awaited()
    assert all(m.get("body") != h["body"] for m in messages)


async def test_nothing_sent_until_commit_acknowledged(harness):
    h = harness
    ready, proceed = asyncio.Event(), asyncio.Event()
    original = h["factory"]

    async def commit():
        ready.set()
        await proceed.wait()
        h["trace"].append("commit")

    def factory():
        db = original()
        if len(h["sessions"]) == 1:
            db.commit.side_effect = commit
        return db

    messages = []
    response = adapter.AuditedRecordResponse(AUTHOR, PATIENT, h["request"], uuid4(), factory)
    work = asyncio.create_task(
        response(SCOPE, receive_forever, AsyncMock(side_effect=messages.append))
    )
    await asyncio.wait_for(ready.wait(), 1)
    assert messages == []
    proceed.set()
    await work
    assert messages[0]["status"] == 200 and messages[1]["body"] == h["body"]
    assert len(h["sessions"]) == 2
    h["sessions"][1].commit.assert_not_awaited()
    h["sessions"][1].rollback.assert_awaited_once()


async def test_base_response_mutation_cannot_bypass_bound_bytes(harness):
    response = harness["response"]()
    response.body = b"SYNTHETIC_UNBOUND_BODY"
    response.headers["x-private-value"] = "SYNTHETIC_UNBOUND_HEADER"
    messages = []
    await response(SCOPE, receive_forever, AsyncMock(side_effect=messages.append))
    assert messages[1]["body"] == harness["body"]
    assert b"x-private-value" not in dict(messages[0]["headers"])
    assert int(dict(messages[0]["headers"])[b"content-length"]) == len(harness["body"])


async def test_preexisting_transaction_is_not_committed_by_orchestrator(harness):
    original = harness["factory"]

    def factory():
        db = original()
        db.in_transaction.return_value = True
        return db

    response = adapter.AuditedRecordResponse(AUTHOR, PATIENT, harness["request"], uuid4(), factory)
    messages = []
    await response(SCOPE, receive_forever, AsyncMock(side_effect=messages.append))
    assert messages[0]["status"] == 503
    harness["sessions"][0].commit.assert_not_awaited()
    harness["prepare"].assert_not_awaited()


async def test_second_phase_cannot_reuse_first_session_object(harness):
    original = harness["factory"]

    def factory():
        return harness["sessions"][0] if harness["sessions"] else original()

    messages = []
    response = adapter.AuditedRecordResponse(AUTHOR, PATIENT, harness["request"], uuid4(), factory)
    await response(SCOPE, receive_forever, AsyncMock(side_effect=messages.append))
    assert messages[0]["status"] == 503
    harness["validate"].assert_not_awaited()


async def test_withheld_gate_commits_evaluation_without_intent(harness):
    h = harness
    h["prepare"].side_effect = None
    h["prepare"].return_value = service._Prepared(None)
    messages = []
    await h["response"]()(SCOPE, receive_forever, AsyncMock(side_effect=messages.append))
    assert messages[0]["status"] == 409
    assert h["trace"] == ["commit"]
    assert len(h["sessions"]) == 1


@pytest.mark.parametrize("status", [403, 409, 503])
async def test_fresh_revalidation_failure_suppresses_body(harness, status):
    h = harness
    h["validate"].side_effect = HTTPException(status, "SYNTHETIC_PRIVATE_DETAIL")
    messages = []
    await h["response"]()(SCOPE, receive_forever, AsyncMock(side_effect=messages.append))
    assert messages[0]["status"] == status
    assert all(m.get("body") != h["body"] for m in messages)
    assert h["trace"] == ["commit", "rollback"]


async def test_disconnect_before_commit_rolls_back_without_sending(harness):
    messages = []
    with pytest.raises(asyncio.CancelledError):
        await harness["response"]()(
            SCOPE,
            AsyncMock(return_value={"type": "http.disconnect"}),
            AsyncMock(side_effect=messages.append),
        )
    assert not messages
    harness["sessions"][0].commit.assert_not_awaited()
    harness["sessions"][0].rollback.assert_awaited_once()


@pytest.mark.parametrize("cause", ["timeout", "cancel", "database", "disconnect"])
async def test_owned_send_work_finishes_before_rollback(harness, monkeypatch, cause):
    h = harness
    entered, disconnect = asyncio.Event(), asyncio.Event()
    messages = []

    async def send(message):
        if message["type"] == "http.response.body":
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                h["trace"].append("send_finished")
        else:
            messages.append(message)

    async def receive():
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def identity(db):
        if cause == "database" and entered.is_set():
            raise SQLAlchemyError("SYNTHETIC_CONNECTION_LOST")
        return 1, 1

    h["identity"].side_effect = identity
    response = h["response"](handoff_timeout=0.08)
    work = asyncio.create_task(response(SCOPE, receive, send))
    await asyncio.wait_for(entered.wait(), 1)
    if cause == "cancel":
        work.cancel()
    if cause == "disconnect":
        disconnect.set()
    if cause == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await work
    else:
        await work
    assert h["trace"].index("send_finished") < h["trace"].index("rollback")
    assert len(messages) == 1  # No second response after start may have escaped.


async def test_catalogue_change_between_headers_and_body_aborts_remaining_send(
    harness, monkeypatch
):
    h = harness
    messages = []

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.start":
            monkeypatch.setattr(adapter, "catalogue_fingerprint", lambda: "b" * 64)

    await h["response"]()(SCOPE, receive_forever, send)
    assert len(messages) == 1 and messages[0]["status"] == 200


async def test_transaction_replacement_cannot_silently_resume(harness):
    h = harness
    h["identity"].side_effect = [(1, 1), (2, 2)]
    messages = []
    await h["response"]()(SCOPE, receive_forever, AsyncMock(side_effect=messages.append))
    assert messages[0]["status"] == 503 and h["body"] != messages[-1].get("body")


async def test_response_instance_is_one_shot(harness):
    response = harness["response"]()
    await response(SCOPE, receive_forever, AsyncMock())
    with pytest.raises(HTTPException) as error:
        await response(SCOPE, receive_forever, AsyncMock())
    assert error.value.status_code == 409
    assert len(harness["sessions"]) == 2


@pytest.mark.parametrize("phase", ["start", "body"])
async def test_send_failure_never_retries_or_emits_second_response(harness, phase):
    messages = []

    async def send(message):
        messages.append(message)
        if message["type"] == f"http.response.{phase}":
            raise OSError("SYNTHETIC_PRIVATE_SEND_ERROR")

    await harness["response"]()(SCOPE, receive_forever, send)
    assert len(messages) == (1 if phase == "start" else 2)
    assert harness["trace"] == ["commit", "rollback"]


async def test_repeated_cancellation_joins_send_cleanup_before_database_release(harness):
    entered, cleaning, finish_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def send(message):
        if message["type"] == "http.response.body":
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await finish_cleanup.wait()
                harness["trace"].append("send_finished")

    work = asyncio.create_task(harness["response"]()(SCOPE, receive_forever, send))
    await asyncio.wait_for(entered.wait(), 1)
    work.cancel()
    await asyncio.wait_for(cleaning.wait(), 1)
    work.cancel()
    await asyncio.sleep(0)
    assert "rollback" not in harness["trace"] and not work.done()
    finish_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await work
    assert harness["trace"].index("send_finished") < harness["trace"].index("rollback")


async def test_complete_original_request_is_frozen_before_async_work(harness):
    original = harness["request"].model_dump()
    response = adapter.AuditedRecordResponse(AUTHOR, PATIENT, original, uuid4(), harness["factory"])
    original["draft"]["claims"] = ()
    original["draft"]["unverified_text"] = "SYNTHETIC_CHANGED_PROVIDER_PROSE"
    await response(SCOPE, receive_forever, AsyncMock())
    assert harness["prepare"].call_args.args[3] == harness["request"]
    assert harness["validate"].call_args.args[3] == harness["request"]


@pytest.mark.parametrize("fault", ["inactive", "no_transaction", "invalidated", "closed"])
async def test_known_unhealthy_session_never_reconnects(fault):
    db = MagicMock(spec=AsyncSession)
    db.is_active = fault != "inactive"
    db.in_transaction.return_value = fault != "no_transaction"
    connection = MagicMock()
    connection.closed = fault == "closed"
    connection.invalidated = fault == "invalidated"
    connection.in_transaction.return_value = True
    connection.execute = AsyncMock()
    db.connection = AsyncMock(return_value=connection)
    with pytest.raises(HTTPException) as error:
        await service.transaction_identity(db)
    assert error.value.status_code == 503
    connection.execute.assert_not_awaited()


@pytest.mark.parametrize("changed", ["body", "snapshot", "policy", "catalogue"])
async def test_revalidation_requires_exact_committed_binding(monkeypatch, changed):
    from app.schemas.verification_gate import GateRun
    from app.services.verification_audit import event_output, project_gate
    from tests.test_verification_audit import stored

    row = fact()
    request = composed(claim_for(row))
    gate = gated(request, [row])
    event = event_output(stored(project_gate(gate, AUTHOR, "patient")))
    digest = service.body_digest(service.encode_records(gate))
    receipt = ReleaseIntent(
        id=uuid4(),
        evaluation_event_id=event.id,
        release_policy_version=RELEASE_POLICY,
        representation_version=REPRESENTATION,
        body_digest="0" * 64 if changed == "body" else digest,
        recorded_at=datetime.now(UTC),
    )
    if changed == "snapshot":
        gate = gate.model_copy(
            update={
                "verification": gate.verification.model_copy(
                    update={
                        "snapshot": gate.verification.snapshot.model_copy(
                            update={"history_version_ids": ()}
                        ),
                    }
                )
            }
        )
    elif changed == "policy":
        gate = gate.model_copy(update={"policy_version": "future-policy"})
    monkeypatch.setattr(
        service,
        "evaluate_patient_gate",
        AsyncMock(return_value=GateRun(evaluated_at=datetime.now(UTC), gate=gate)),
    )
    monkeypatch.setattr(service, "get_release_intent", AsyncMock(return_value=receipt))
    monkeypatch.setattr(service, "get_patient_audit_event", AsyncMock(return_value=event))
    monkeypatch.setattr(service, "authorize_patient_scope", AsyncMock())
    monkeypatch.setattr(
        service,
        "catalogue_fingerprint",
        lambda: (
            None if changed == "catalogue" else event.metadata.verification.catalogue_fingerprint
        ),
    )
    async with AsyncSession() as db:
        with pytest.raises(HTTPException) as error:
            await service.revalidate_release(
                db, AUTHOR, PATIENT, request, service._Prepared(receipt)
            )
    assert error.value.status_code == (503 if changed == "catalogue" else 409)


@pytest.mark.parametrize("timeout", [0, -1, 6, float("inf"), float("nan"), True])
def test_timeout_is_bounded_configuration(harness, timeout):
    with pytest.raises(ValueError):
        harness["response"](handoff_timeout=timeout)


async def test_database_atomic_preparation_durability_and_no_replay(db, api):
    actor, patient, _, request, _ = await setup_patient_fact(db, api)
    operation = uuid4()
    prepared = await service.prepare_release(db, actor, patient, request, operation)
    assert prepared.intent is not None and db.in_transaction()
    await db.commit()
    assert (
        await service.get_release_intent(db, actor, patient, prepared.intent.id)
    ) == prepared.intent
    body, _ = await service.revalidate_release(db, actor, patient, request, prepared)
    assert service.body_digest(body) == prepared.intent.body_digest
    with pytest.raises(HTTPException) as error:
        await service.prepare_release(db, actor, patient, request, operation)
    assert error.value.status_code == 409
    await db.rollback()
    another = await service.prepare_release(db, actor, patient, request, uuid4())
    await db.rollback()
    assert (
        await db.scalar(
            select(ResponseReleaseIntent.id).where(ResponseReleaseIntent.id == another.intent.id)
        )
        is None
    )
    assert await db.scalar(select(func.count()).select_from(VerificationAuditEvent)) == 1
    await db.rollback()


@pytest.mark.parametrize("missing", [True, False])
async def test_database_withheld_evaluation_has_no_intent(db, api, missing):
    actor, patient, _, _, row = await setup_patient_fact(db, api)
    request = composed(claim_for(row, proposed_content=None if missing else "synthetic prose"))
    prepared = await service.prepare_release(db, actor, patient, request, uuid4())
    assert prepared.intent is None
    await db.commit()
    assert await db.scalar(select(func.count()).select_from(VerificationAuditEvent)) == 1
    assert await db.scalar(select(func.count()).select_from(ResponseReleaseIntent)) == 0
    await db.rollback()


@pytest.mark.parametrize("operation", ["update", "delete", "truncate"])
async def test_database_intents_append_only(db, api, operation):
    actor, patient, _, request, _ = await setup_patient_fact(db, api)
    prepared = await service.prepare_release(db, actor, patient, request, uuid4())
    await db.commit()
    with pytest.raises(DBAPIError) as error:
        async with db.begin_nested():
            statement = (
                update(ResponseReleaseIntent).values(body_digest="0" * 64)
                if operation == "update"
                else delete(ResponseReleaseIntent)
                if operation == "delete"
                else text("TRUNCATE response_release_intents")
            )
            await db.execute(statement.execution_options(synchronize_session=False))
    assert "append-only" in str(error.value.orig)
    assert await db.scalar(select(ResponseReleaseIntent.id)) == prepared.intent.id
    await db.rollback()


async def test_database_parent_must_be_pass_and_unique(db, api):
    actor, patient, _, request, row = await setup_patient_fact(db, api)
    withheld = composed(claim_for(row, proposed_content=None))
    await service.prepare_release(db, actor, patient, withheld, uuid4())
    await db.commit()
    event = await db.scalar(select(VerificationAuditEvent))
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(
                ResponseReleaseIntent(
                    evaluation_event_id=event.id,
                    release_policy_version=RELEASE_POLICY,
                    representation_version=REPRESENTATION,
                    body_digest="0" * 64,
                )
            )
            await db.flush()
    await db.rollback()
    prepared = await service.prepare_release(db, actor, patient, request, uuid4())
    await db.commit()
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(
                ResponseReleaseIntent(
                    evaluation_event_id=prepared.intent.evaluation_event_id,
                    release_policy_version=RELEASE_POLICY,
                    representation_version=REPRESENTATION,
                    body_digest="0" * 64,
                )
            )
            await db.flush()
    await db.rollback()


async def test_database_lookup_actor_only_and_revocation(db, api):
    actor, patient_id, _, request, _ = await setup_patient_fact(db, api)
    patient = await db.get(User, actor)
    doctor, practitioner = await add_practitioner(db)
    doctor_id, practitioner_id = doctor.id, practitioner.id
    await db.commit()
    assert (await grant(api, patient, practitioner_id)).status_code == 200
    own = await service.prepare_release(db, actor, patient_id, request, uuid4())
    clinical = await service.prepare_release(db, doctor_id, patient_id, request, uuid4())
    await db.commit()
    for account, other in ((actor, clinical), (doctor_id, own)):
        with pytest.raises(HTTPException) as error:
            await service.get_release_intent(db, account, patient_id, other.intent.id)
        assert error.value.status_code == 404
    await db.rollback()
    await db.refresh(patient)
    assert (await revoke(api, patient, practitioner_id)).status_code == 200
    with pytest.raises(HTTPException) as error:
        await service.get_release_intent(db, doctor_id, patient_id, clinical.intent.id)
    assert error.value.status_code == 403
    await db.rollback()


async def test_database_adapter_durable_before_first_header_and_sends_exact_body(
    db, race_environment
):
    env = race_environment
    actor = decode_access_token(env["doctor_auth"]["Authorization"].split()[1])
    from app.models import ClinicalFactVersion

    row = version_input(
        await db.scalar(
            select(ClinicalFactVersion).where(ClinicalFactVersion.patient_id == env["patient_id"])
        )
    )
    request = composed(claim_for(row))
    factory = async_sessionmaker(env["engine"], expire_on_commit=False)
    messages = []

    async def send(message):
        if message["type"] == "http.response.start":
            async with factory() as observer:
                assert (
                    await observer.scalar(select(func.count()).select_from(ResponseReleaseIntent))
                    == 1
                )
                assert (
                    await observer.scalar(select(func.count()).select_from(VerificationAuditEvent))
                    == 1
                )
        messages.append(message)

    response = adapter.AuditedRecordResponse(actor, env["patient_id"], request, uuid4(), factory)
    await response(SCOPE, receive_forever, send)
    assert messages[0]["status"] == 200
    intent = await db.scalar(select(ResponseReleaseIntent))
    assert service.body_digest(messages[1]["body"]) == intent.body_digest
    assert set(json.loads(messages[1]["body"])) == {"representation_version", "records"}
    await db.rollback()


@pytest.mark.parametrize("change", ["revoke", "correct", "catalogue"])
async def test_database_gap_changes_suppress_protected_body(
    db, race_environment, monkeypatch, change
):
    env = race_environment
    actor = decode_access_token(env["doctor_auth"]["Authorization"].split()[1])
    from app.models import ClinicalFactVersion

    row = version_input(
        await db.scalar(
            select(ClinicalFactVersion).where(ClinicalFactVersion.patient_id == env["patient_id"])
        )
    )
    request = composed(claim_for(row))
    original = adapter.revalidate_release

    async def changed(*args):
        if change == "catalogue":
            monkeypatch.setattr(service, "catalogue_fingerprint", lambda: None)
            return await original(*args)
        async with AsyncClient(
            transport=ASGITransport(app=env["app"]), base_url="http://test"
        ) as client:
            if change == "revoke":
                result = await client.post(
                    f"/api/v1/patients/me/consents/{env['doctor_profile_id']}/revoke",
                    headers=env["auth"],
                )
            else:
                result = await client.post(
                    f"{path(env['patient_id'])}/{row.fact_id}/corrections",
                    headers=env["auth"],
                    json={
                        **entry("7.4"),
                        "expected_version_id": str(row.version_id),
                        "correction_reason": "synthetic correction",
                    },
                )
            assert result.status_code in (200, 201)
        return await original(*args)

    monkeypatch.setattr(adapter, "revalidate_release", changed)
    messages = []
    response = adapter.AuditedRecordResponse(
        actor,
        env["patient_id"],
        request,
        uuid4(),
        async_sessionmaker(env["engine"], expire_on_commit=False),
    )
    await response(SCOPE, receive_forever, AsyncMock(side_effect=messages.append))
    assert messages[0]["status"] == {"revoke": 403, "correct": 409, "catalogue": 503}[change]
    assert all(b'"records"' not in m.get("body", b"") for m in messages)


@pytest.mark.parametrize("change", ["revoke", "correct"])
async def test_database_mutations_wait_for_owned_handoff(db, race_environment, change):
    env = race_environment
    actor = decode_access_token(env["doctor_auth"]["Authorization"].split()[1])
    from app.models import ClinicalFactVersion

    row = version_input(
        await db.scalar(
            select(ClinicalFactVersion).where(ClinicalFactVersion.patient_id == env["patient_id"])
        )
    )
    ready, proceed = asyncio.Event(), asyncio.Event()
    factory = async_sessionmaker(env["engine"], expire_on_commit=False)

    async def send(message):
        if message["type"] == "http.response.body":
            ready.set()
            await proceed.wait()

    response = adapter.AuditedRecordResponse(
        actor, env["patient_id"], composed(claim_for(row)), uuid4(), factory
    )
    handing = asyncio.create_task(response(SCOPE, receive_forever, send))
    await asyncio.wait_for(ready.wait(), 3)
    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        if change == "revoke":
            mutation = asyncio.create_task(
                client.post(
                    f"/api/v1/patients/me/consents/{env['doctor_profile_id']}/revoke",
                    headers=env["auth"],
                )
            )
        else:
            mutation = asyncio.create_task(
                client.post(
                    f"{path(env['patient_id'])}/{row.fact_id}/corrections",
                    headers=env["auth"],
                    json={
                        **entry("7.4"),
                        "expected_version_id": str(row.version_id),
                        "correction_reason": "synthetic correction",
                    },
                )
            )
        try:
            await wait_until_blocked(db, env, "revoke" if change == "revoke" else "clinical")
            assert not mutation.done()
        finally:
            proceed.set()
        await handing
        assert (await mutation).status_code == (200 if change == "revoke" else 201)


async def test_database_intent_flush_failure_rolls_back_evaluation_and_intent(
    db, race_environment, monkeypatch
):
    from app.models import ClinicalFactVersion

    env = race_environment
    actor = decode_access_token(env["doctor_auth"]["Authorization"].split()[1])
    row = version_input(
        await db.scalar(
            select(ClinicalFactVersion).where(ClinicalFactVersion.patient_id == env["patient_id"])
        )
    )
    monkeypatch.setattr(service, "body_digest", lambda body: "invalid")
    messages = []
    response = adapter.AuditedRecordResponse(
        actor,
        env["patient_id"],
        composed(claim_for(row)),
        uuid4(),
        async_sessionmaker(env["engine"], expire_on_commit=False),
    )
    await response(SCOPE, receive_forever, AsyncMock(side_effect=messages.append))
    assert messages[0]["status"] == 503
    assert await db.scalar(select(func.count()).select_from(ResponseReleaseIntent)) == 0
    assert await db.scalar(select(func.count()).select_from(VerificationAuditEvent)) == 0
    await db.rollback()


async def test_database_concurrent_duplicate_requests_have_one_owner(db, race_environment):
    from app.models import ClinicalFactVersion

    env = race_environment
    actor = decode_access_token(env["doctor_auth"]["Authorization"].split()[1])
    row = version_input(
        await db.scalar(
            select(ClinicalFactVersion).where(ClinicalFactVersion.patient_id == env["patient_id"])
        )
    )
    request, operation = composed(claim_for(row)), uuid4()
    factory = async_sessionmaker(env["engine"], expire_on_commit=False)
    first, second = [], []
    await asyncio.gather(
        adapter.AuditedRecordResponse(actor, env["patient_id"], request, operation, factory)(
            SCOPE, receive_forever, AsyncMock(side_effect=first.append)
        ),
        adapter.AuditedRecordResponse(actor, env["patient_id"], request, operation, factory)(
            SCOPE, receive_forever, AsyncMock(side_effect=second.append)
        ),
    )
    assert sorted([first[0]["status"], second[0]["status"]]) == [200, 409]
    assert await db.scalar(select(func.count()).select_from(ResponseReleaseIntent)) == 1
    assert await db.scalar(select(func.count()).select_from(VerificationAuditEvent)) == 1
    await db.rollback()
