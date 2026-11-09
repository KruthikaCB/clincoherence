"""Metadata-only history: evaluation is never durability or release authorization."""

import asyncio
import secrets
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import decode_access_token
from app.models import ClinicalFactVersion, ConfirmationDecision, User, VerificationAuditEvent
from app.schemas.verification_audit import AuditMetadata
from app.schemas.verification_gate import GateRun
from app.services import verification_audit as service
from app.services.consistency import version_input
from app.services.evidence_catalog import canonical_hash
from tests.database import migration
from tests.test_auth import add_patient, add_practitioner, headers
from tests.test_clinical_facts import entry, path, wait_until_blocked
from tests.test_clinical_facts import race_environment as race_environment
from tests.test_consents import grant, revoke
from tests.test_consistency import AUTHOR, PATIENT, fact
from tests.test_draft_verification import claim_for
from tests.test_evidence_aware_draft import composed
from tests.test_verification_gate import gated


@pytest.fixture(autouse=True)
def signing_configuration(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", secrets.token_urlsafe(48))


def metadata_for(*, decision="PASS", bound=True):
    row = fact()
    changes = {
        "CLARIFY": {"proposed_content": None},
        "REFUSE": {"claim_type": "CLINICAL_INTERPRETATION"},
    }
    request = composed(claim_for(row, **changes.get(decision, {})), bound=bound)
    gate = gated(request, [row])
    return service.project_gate(gate, AUTHOR, "patient")


def stored(metadata=None, **changes):
    metadata = metadata or metadata_for()
    payload = metadata.model_dump(mode="json")
    fields = dict(
        id=uuid4(),
        operation_id=uuid4(),
        patient_id=metadata.patient_id,
        actor_id=metadata.actor_id,
        actor_role=metadata.actor_role,
        event_kind=metadata.event_kind,
        audit_schema_version=metadata.audit_schema_version,
        gate_decision=metadata.decision,
        payload=payload,
        payload_hash=canonical_hash(payload),
        evaluated_at=datetime.now(UTC),
        recorded_at=datetime.now(UTC),
    )
    fields.update(changes)
    return VerificationAuditEvent(**fields)


@pytest.mark.parametrize("decision", ["PASS", "CLARIFY", "REFUSE"])
@pytest.mark.parametrize("bound", [True, False])
def test_all_gate_decisions_preserve_metadata_without_text(decision, bound):
    metadata = metadata_for(decision=decision, bound=bound)
    assert metadata.decision == decision
    assert metadata.release_status == "NOT_ASSERTED"
    assert not metadata.http_release_authorized and not metadata.unrestricted_text_verified
    claim = metadata.claims[0]
    assert (
        claim.grounding.outcome
        == {"PASS": "GROUNDED", "CLARIFY": "INVALID", "REFUSE": "UNSUPPORTED"}[decision]
    )
    assert (claim.evidence is not None) == bound
    if bound:
        assert claim.evidence.integrity.status == "RESOLVED"
        assert claim.evidence.integrity.evidence[0].source_content_hash
        assert claim.evidence.approval_manifest_hash
    assert metadata.verification.snapshot.current_version_ids == (fact().version_id,)
    for excluded in (
        "rendered_text",
        "rendered_records",
        "proposed_content",
        "source_quote",
        "password",
        "text_verified",
    ):
        assert excluded not in metadata.model_dump() and excluded not in claim.model_dump()


def test_raw_source_prose_never_enters_projection():
    marker = "SYNTHETIC_PRIVATE_TEXT_DO_NOT_PERSIST"
    row = fact(fact={"kind": "history", "category": "family_history", "description": marker})
    request = composed(claim_for(row), bound=False)
    gate = gated(request, [row])
    assert marker in gate.rendered_records[0].text
    metadata = service.project_gate(gate, AUTHOR, "patient")
    assert marker not in metadata.model_dump_json()
    data = request.model_dump()
    data["draft"]["unverified_text"] = marker
    request = type(request).model_validate(data)
    refused = service.project_gate(gated(request, [row]), AUTHOR, "patient")
    assert refused.decision == "REFUSE" and marker not in refused.model_dump_json()


@pytest.mark.parametrize(
    "field", ["rendered_records", "prompt", "text", "token", "password", "approved", "evaluated_at"]
)
def test_metadata_forbids_unallowlisted_content(field):
    data = metadata_for().model_dump()
    data[field] = "SYNTHETIC_SECRET_MARKER"
    with pytest.raises(ValidationError):
        AuditMetadata.model_validate(data)


@pytest.mark.parametrize("level", ["claim", "grounding", "integrity", "snapshot", "binding"])
def test_nested_whitelist_rejects_content(level):
    data = metadata_for().model_dump()
    target = {
        "claim": data["claims"][0],
        "grounding": data["claims"][0]["grounding"],
        "integrity": data["claims"][0]["evidence"]["integrity"],
        "snapshot": data["verification"]["snapshot"],
        "binding": data["claims"][0]["evidence"]["binding"],
    }[level]
    target["raw_text"] = "SYNTHETIC_PRIVATE_MARKER"
    with pytest.raises(ValidationError):
        AuditMetadata.model_validate(data)


def test_frozen_strict_metadata_and_deterministic_identity():
    metadata = metadata_for()
    before = metadata.model_dump_json()
    assert metadata == metadata_for()
    with pytest.raises(ValidationError):
        metadata.claims[0].decision = "WARN"
    with pytest.raises(ValidationError):
        AuditMetadata.model_validate({**metadata.model_dump(), "claims": list(metadata.claims)})
    first, second = stored(metadata), stored(metadata)
    assert first.id != second.id and first.payload_hash == second.payload_hash
    second.evaluated_at += timedelta(days=1)
    assert service.event_output(first).metadata == service.event_output(second).metadata
    assert before == metadata.model_dump_json()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("decision", "WARN"),
        ("release_status", "HTTP_RELEASED"),
        ("http_release_authorized", True),
        ("unrestricted_text_verified", True),
    ],
)
def test_evaluation_metadata_cannot_assert_release_or_warning(field, value):
    with pytest.raises(ValidationError):
        AuditMetadata.model_validate({**metadata_for().model_dump(), field: value})


@pytest.mark.parametrize("change", ["scope", "hash", "decision", "payload_extra", "role"])
def test_stored_identity_and_hash_revalidated_without_echo(change):
    row = stored()
    if change == "scope":
        row.patient_id = uuid4()
    elif change == "hash":
        row.payload_hash = "0" * 64
    elif change == "decision":
        row.gate_decision = "REFUSE"
    elif change == "role":
        row.actor_role = "practitioner"
    else:
        row.payload["secret_text"] = "SYNTHETIC_PRIVATE_MARKER"
    with pytest.raises(HTTPException) as error:
        service.event_output(row)
    assert error.value.status_code == 503
    assert "SYNTHETIC_PRIVATE_MARKER" not in error.value.detail


def test_migration_dependency_and_metadata_registration():
    revision = migration("0006_verification_audit")
    assert revision.down_revision == "0005_supervised_extraction"
    table = VerificationAuditEvent.__table__
    assert table.name == "verification_audit_events"
    assert len(table.indexes) == 1
    assert {fk.referred_table.name for fk in table.foreign_key_constraints} == {
        "users",
        "patient_profiles",
    }


@pytest.fixture
async def mock_writer(monkeypatch):
    row = fact()
    request = composed(claim_for(row))
    run = GateRun(evaluated_at=datetime.now(UTC), gate=gated(request, [row]))
    evaluator = AsyncMock(return_value=run)
    monkeypatch.setattr(service, "evaluate_patient_gate", evaluator)
    monkeypatch.setattr(
        service,
        "eligible_account",
        AsyncMock(return_value=SimpleNamespace(id=AUTHOR, role="patient")),
    )
    monkeypatch.setattr(service, "authorize_patient_scope", AsyncMock())
    async with AsyncSession() as db:
        inserted = []
        db.scalar = AsyncMock(return_value=None)
        db.add = Mock(side_effect=inserted.append)

        async def flush():
            for event in inserted:
                event.id = event.id or uuid4()
                event.recorded_at = datetime.now(UTC)

        db.flush = AsyncMock(side_effect=flush)
        db.commit, db.rollback = AsyncMock(), AsyncMock()
        yield db, request, evaluator, inserted


async def test_writer_independently_evaluates_and_never_commits(mock_writer):
    db, request, evaluator, inserted = mock_writer
    operation = uuid4()
    event = await service.record_patient_gate_evaluation(db, AUTHOR, PATIENT, request, operation)
    evaluator.assert_awaited_once_with(db, AUTHOR, PATIENT, request)
    assert len(inserted) == 1 and event.operation_id == operation
    assert event.metadata.actor_id == AUTHOR and event.metadata.actor_role == "patient"
    db.flush.assert_awaited_once()
    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()


async def test_authorized_identical_retry_returns_original_event(mock_writer):
    db, request, evaluator, inserted = mock_writer
    existing = stored()
    db.scalar.return_value = existing
    event = await service.record_patient_gate_evaluation(
        db, AUTHOR, PATIENT, request, existing.operation_id
    )
    assert event.id == existing.id and not inserted
    evaluator.assert_awaited_once()
    db.flush.assert_not_awaited()


async def test_conflicting_retry_never_overwrites(mock_writer):
    db, request, _, inserted = mock_writer
    existing = stored(metadata_for(decision="CLARIFY"))
    db.scalar.return_value = existing
    with pytest.raises(HTTPException) as error:
        await service.record_patient_gate_evaluation(
            db, AUTHOR, PATIENT, request, existing.operation_id
        )
    assert error.value.status_code == 409 and "rollback required" in error.value.detail
    assert not inserted
    db.flush.assert_not_awaited()


async def test_flush_failure_fails_closed_without_hidden_rollback(mock_writer):
    db, request, _, _ = mock_writer
    db.flush.side_effect = SQLAlchemyError("SYNTHETIC_PRIVATE_ERROR_DETAIL")
    with pytest.raises(HTTPException) as error:
        await service.record_patient_gate_evaluation(db, AUTHOR, PATIENT, request, uuid4())
    assert error.value.status_code == 503 and "rollback required" in error.value.detail
    assert "PRIVATE_ERROR" not in error.value.detail
    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()


async def test_invalid_projection_fails_before_insert(mock_writer, monkeypatch):
    db, request, _, inserted = mock_writer
    gate = gated(request, [fact()]).model_copy(update={"policy_version": "SYNTHETIC PRIVATE TEXT"})
    monkeypatch.setattr(
        service,
        "evaluate_patient_gate",
        AsyncMock(
            return_value=GateRun(
                evaluated_at=datetime.now(UTC),
                gate=gate,
            )
        ),
    )
    with pytest.raises(HTTPException) as error:
        await service.record_patient_gate_evaluation(db, AUTHOR, PATIENT, request, uuid4())
    assert error.value.status_code == 503 and "rollback required" in error.value.detail
    assert not inserted and "PRIVATE TEXT" not in error.value.detail
    db.flush.assert_not_awaited()


async def test_unauthorized_attempt_creates_no_patient_event(mock_writer):
    db, request, evaluator, inserted = mock_writer
    evaluator.side_effect = HTTPException(403, "Patient scope denied")
    with pytest.raises(HTTPException) as error:
        await service.record_patient_gate_evaluation(db, AUTHOR, PATIENT, request, uuid4())
    assert error.value.status_code == 403 and not inserted
    db.scalar.assert_not_awaited()
    db.flush.assert_not_awaited()


async def test_internal_reader_is_actor_and_patient_filtered(monkeypatch):
    async with AsyncSession() as db:
        row = stored()
        monkeypatch.setattr(
            service, "lock_patient_scope", AsyncMock(return_value=SimpleNamespace(id=AUTHOR))
        )
        monkeypatch.setattr(service, "authorize_patient_scope", AsyncMock())
        db.scalar = AsyncMock(return_value=row)
        event = await service.get_patient_audit_event(db, AUTHOR, PATIENT, row.id)
        assert event.id == row.id
        query = str(db.scalar.call_args.args[0])
        assert "patient_id =" in query and "actor_id =" in query and ".id =" in query
        db.scalars = AsyncMock(return_value=[row])
        assert (await service.list_patient_audit_events(db, AUTHOR, PATIENT))[0].id == row.id
        assert "LIMIT" in str(db.scalars.call_args.args[0])


@pytest.mark.parametrize("limit", [0, 101, True, "1"])
async def test_reader_limits_are_validated_after_authorization(monkeypatch, limit):
    locked = AsyncMock(return_value=SimpleNamespace(id=AUTHOR))
    monkeypatch.setattr(service, "lock_patient_scope", locked)
    async with AsyncSession() as db:
        db.scalars = AsyncMock()
        with pytest.raises(HTTPException) as error:
            await service.list_patient_audit_events(db, AUTHOR, PATIENT, limit=limit)
        assert error.value.status_code == 422
        locked.assert_awaited_once()
        db.scalars.assert_not_awaited()


async def test_reader_rejects_dirty_session_without_autoflush(monkeypatch):
    locked = AsyncMock()
    monkeypatch.setattr(service, "lock_patient_scope", locked)
    async with AsyncSession() as db:
        db.add(
            User(identity="synthetic@fixture.invalid", hashed_password="synthetic", role="patient")
        )
        db.flush = AsyncMock()
        with pytest.raises(HTTPException) as error:
            await service.list_patient_audit_events(db, AUTHOR, PATIENT)
        assert error.value.status_code == 409
        locked.assert_not_awaited()
        db.flush.assert_not_awaited()


async def setup_patient_fact(db, api):
    patient, profile = await add_patient(db)
    account_id, patient_id, auth = patient.id, profile.id, headers(patient)
    response = await api.post(path(patient_id), headers=auth, json=entry())
    assert response.status_code == 201
    row = version_input(await db.get(ClinicalFactVersion, UUID(response.json()["version_id"])))
    return account_id, patient_id, auth, composed(claim_for(row)), row


async def test_database_commit_rollback_and_idempotency(db, api):
    actor, patient, _, request, _ = await setup_patient_fact(db, api)
    operation = uuid4()
    event = await service.record_patient_gate_evaluation(db, actor, patient, request, operation)
    assert db.in_transaction() and not db.new and not db.dirty
    assert (
        await service.record_patient_gate_evaluation(db, actor, patient, request, operation)
    ).id == event.id
    await db.commit()
    assert (await service.get_patient_audit_event(db, actor, patient, event.id)) == event
    assert (
        await service.record_patient_gate_evaluation(db, actor, patient, request, operation)
    ).id == event.id
    draft = request.model_copy(
        update={
            "draft": request.draft.model_copy(
                update={"unverified_text": "synthetic withheld prose"}
            )
        }
    )
    with pytest.raises(HTTPException) as error:
        await service.record_patient_gate_evaluation(db, actor, patient, draft, operation)
    assert error.value.status_code == 409
    await db.rollback()
    another = await service.record_patient_gate_evaluation(db, actor, patient, request, uuid4())
    await db.rollback()
    with pytest.raises(HTTPException) as error:
        await service.get_patient_audit_event(db, actor, patient, another.id)
    assert error.value.status_code == 404
    assert await db.scalar(select(func.count()).select_from(VerificationAuditEvent)) == 1
    await db.rollback()


async def test_database_failed_flush_requires_rollback(db, api, monkeypatch):
    actor, patient, _, request, _ = await setup_patient_fact(db, api)
    original_add = db.add

    def invalid_add(row):
        row.payload_hash = "invalid"
        original_add(row)

    monkeypatch.setattr(db, "add", invalid_add)
    with pytest.raises(HTTPException) as error:
        await service.record_patient_gate_evaluation(db, actor, patient, request, uuid4())
    assert error.value.status_code == 503 and "rollback required" in error.value.detail
    assert not db.is_active
    await db.rollback()
    assert await db.scalar(select(func.count()).select_from(VerificationAuditEvent)) == 0
    await db.rollback()


@pytest.mark.parametrize("operation", ["update", "delete", "truncate"])
async def test_database_append_only(db, api, operation):
    actor, patient, _, request, _ = await setup_patient_fact(db, api)
    event = await service.record_patient_gate_evaluation(db, actor, patient, request, uuid4())
    await db.commit()
    with pytest.raises(DBAPIError) as error:
        async with db.begin_nested():
            if operation == "truncate":
                await db.execute(text("TRUNCATE verification_audit_events"))
            else:
                statement = (
                    update(VerificationAuditEvent).values(gate_decision="REFUSE")
                    if operation == "update"
                    else delete(VerificationAuditEvent)
                )
                await db.execute(statement.execution_options(synchronize_session=False))
    message = str(error.value.orig)
    assert "append-only" in message or (
        operation == "truncate"
        and "cannot truncate a table referenced in a foreign key constraint" in message
    )
    assert (await service.get_patient_audit_event(db, actor, patient, event.id)).id == event.id
    await db.rollback()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("patient_id", "missing"),
        ("actor_id", "missing"),
        ("actor_role", "practitioner"),
        ("gate_decision", "WARN"),
        ("payload_hash", "invalid"),
        ("event_kind", "HTTP_RELEASED"),
        ("audit_schema_version", "unknown"),
        ("payload", {}),
    ],
)
async def test_database_constraints(db, api, field, value):
    actor, patient, _, request, _ = await setup_patient_fact(db, api)
    metadata = service.project_gate(
        gated(request, [version_input(await db.scalar(select(ClinicalFactVersion)))]),
        actor,
        "patient",
    )
    row = stored(metadata, **{field: uuid4() if value == "missing" else value})
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    await db.rollback()


async def test_database_actor_only_and_revocation(db, api):
    actor, patient_id, _, request, _ = await setup_patient_fact(db, api)
    patient = await db.get(User, actor)
    doctor, practitioner = await add_practitioner(db)
    other, _ = await add_patient(db)
    doctor_id, practitioner_id, other_id = doctor.id, practitioner.id, other.id
    await db.commit()
    for denied in (doctor_id, other_id):
        with pytest.raises(HTTPException) as error:
            await service.record_patient_gate_evaluation(db, denied, patient_id, request, uuid4())
        assert error.value.status_code == 403
    assert await db.scalar(select(func.count()).select_from(VerificationAuditEvent)) == 0
    await db.rollback()
    await db.refresh(patient)
    assert (await grant(api, patient, practitioner_id)).status_code == 200
    own = await service.record_patient_gate_evaluation(db, actor, patient_id, request, uuid4())
    clinical = await service.record_patient_gate_evaluation(
        db, doctor_id, patient_id, request, uuid4()
    )
    await db.commit()
    assert [e.id for e in await service.list_patient_audit_events(db, actor, patient_id)] == [
        own.id
    ]
    assert [e.id for e in await service.list_patient_audit_events(db, doctor_id, patient_id)] == [
        clinical.id
    ]
    for account, foreign in ((actor, clinical), (doctor_id, own)):
        with pytest.raises(HTTPException) as error:
            await service.get_patient_audit_event(db, account, patient_id, foreign.id)
        assert error.value.status_code == 404
    await db.rollback()
    await db.refresh(patient)
    assert (await revoke(api, patient, practitioner_id)).status_code == 200
    with pytest.raises(HTTPException) as error:
        await service.get_patient_audit_event(db, doctor_id, patient_id, clinical.id)
    assert error.value.status_code == 403
    assert await db.scalar(select(func.count()).select_from(VerificationAuditEvent)) == 2
    await db.rollback()


async def test_database_no_fact_or_confirmation_mutation(db, api):
    from tests.test_extraction import decide, extract

    patient, profile = await add_patient(db)
    actor, patient_id, auth = patient.id, profile.id, headers(patient)
    items = (await extract(api, patient_id, auth)).json()["confirmations"]
    assert (await decide(api, patient_id, auth, items[0]["confirmation_id"])).status_code == 200
    decision = await db.get(ConfirmationDecision, UUID(items[0]["confirmation_id"]))
    before_decision = (decision.status, decision.accepted_version_id, decision.decided_by_id)
    rows = {
        r.id: version_input(r).model_dump_json()
        for r in await db.scalars(select(ClinicalFactVersion))
    }
    row = version_input(await db.get(ClinicalFactVersion, decision.accepted_version_id))
    event = await service.record_patient_gate_evaluation(
        db, actor, patient_id, composed(claim_for(row), bound=False), uuid4()
    )
    assert event.metadata.decision == "PASS"
    assert rows == {
        r.id: version_input(r).model_dump_json()
        for r in await db.scalars(select(ClinicalFactVersion))
    }
    assert before_decision == (
        decision.status,
        decision.accepted_version_id,
        decision.decided_by_id,
    )
    await db.rollback()


@pytest.mark.parametrize("revoke_first", [True, False])
@pytest.mark.parametrize("reading", [True, False])
async def test_database_revocation_race(db, race_environment, revoke_first, reading):
    env = race_environment
    env["hold_revoke"] = revoke_first
    actor = decode_access_token(env["doctor_auth"]["Authorization"].split()[1])
    row = version_input(
        await db.scalar(
            select(ClinicalFactVersion).where(ClinicalFactVersion.patient_id == env["patient_id"])
        )
    )
    request = composed(claim_for(row))
    existing = await service.record_patient_gate_evaluation(
        db, actor, env["patient_id"], request, uuid4()
    )
    await db.commit()
    async with (
        AsyncSession(env["engine"], expire_on_commit=False) as session,
        AsyncClient(transport=ASGITransport(app=env["app"]), base_url="http://test") as client,
    ):

        async def recording():
            env["pids"]["clinical"] = await session.scalar(text("SELECT pg_backend_pid()"))
            if reading:
                return await service.get_patient_audit_event(
                    session, actor, env["patient_id"], existing.id
                )
            return await service.record_patient_gate_evaluation(
                session, actor, env["patient_id"], request, uuid4()
            )

        async def revoking():
            return await client.post(
                f"/api/v1/patients/me/consents/{env['doctor_profile_id']}/revoke",
                headers=env["auth"],
            )

        if revoke_first:
            revocation = asyncio.create_task(revoking())
            await asyncio.wait_for(env["commit_ready"].wait(), 3)
            recording_task = asyncio.create_task(recording())
            try:
                await wait_until_blocked(db, env, "clinical")
            finally:
                env["commit_release"].set()
            assert (await revocation).status_code == 200
            with pytest.raises(HTTPException) as error:
                await recording_task
            assert error.value.status_code == 403
            await session.rollback()
        else:
            event = await recording()
            revocation = asyncio.create_task(revoking())
            try:
                await wait_until_blocked(db, env, "revoke")
                assert not revocation.done()
                await session.commit()
            finally:
                await session.rollback()
            assert (await revocation).status_code == 200
            assert (
                await db.scalar(
                    select(VerificationAuditEvent.id).where(VerificationAuditEvent.id == event.id)
                )
                == event.id
            )


async def test_database_concurrent_retries_and_correction(db, race_environment):
    env = race_environment
    actor = decode_access_token(env["doctor_auth"]["Authorization"].split()[1])
    patient_id = env["patient_id"]
    row = version_input(
        await db.scalar(
            select(ClinicalFactVersion).where(ClinicalFactVersion.patient_id == patient_id)
        )
    )
    request, operation = composed(claim_for(row)), uuid4()
    async with (
        AsyncSession(env["engine"], expire_on_commit=False) as first,
        AsyncSession(env["engine"], expire_on_commit=False) as second,
        AsyncClient(transport=ASGITransport(app=env["app"]), base_url="http://test") as client,
    ):
        event = await service.record_patient_gate_evaluation(
            first, actor, patient_id, request, operation
        )

        async def retry():
            env["pids"]["clinical"] = await second.scalar(text("SELECT pg_backend_pid()"))
            return await service.record_patient_gate_evaluation(
                second, actor, patient_id, request, operation
            )

        retrying = asyncio.create_task(retry())
        try:
            await wait_until_blocked(db, env, "clinical")
        finally:
            await first.commit()
        assert (await retrying).id == event.id
        correcting = asyncio.create_task(
            client.post(
                f"{path(patient_id)}/{row.fact_id}/corrections",
                headers=env["auth"],
                json={
                    **entry("7.4"),
                    "expected_version_id": str(row.version_id),
                    "correction_reason": "synthetic correction",
                },
            )
        )
        try:
            await wait_until_blocked(db, env, "clinical")
            assert not correcting.done()
        finally:
            await second.commit()
        assert (await correcting).status_code == 201
        with pytest.raises(HTTPException) as error:
            await service.record_patient_gate_evaluation(
                first, actor, patient_id, request, operation
            )
        assert error.value.status_code == 409
        await first.rollback()
        stale = await service.record_patient_gate_evaluation(
            first, actor, patient_id, request, uuid4()
        )
        assert stale.metadata.decision == "REFUSE"
        assert (
            await service.get_patient_audit_event(first, actor, patient_id, event.id)
        ).metadata.decision == "PASS"
        await first.rollback()
