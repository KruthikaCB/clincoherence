"""Synthetic offline contract/service tests and opt-in PostgreSQL integration."""

import asyncio
import secrets
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError, IntegrityError, InterfaceError, OperationalError

from app.api.dependencies import current_account
from app.api.v1 import clinical_fact_reviews as api_module
from app.core.config import settings
from app.core.database import get_db
from app.main import create_app
from app.models import ClinicalFactReviewEvent as Event
from app.schemas.clinical_fact_review import CreateClinicalFactReview
from app.services import clinical_fact_review as service
from tests.database import migration
from tests.test_auth import add_patient, add_practitioner, headers
from tests.test_clinical_facts import (
    entry,
    path,
    race_environment,  # noqa: F401 -- registers the existing isolated PostgreSQL fixture
    wait_until_blocked,
)
from tests.test_consents import grant, revoke


@pytest.fixture(autouse=True)
def signing_configuration(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", secrets.token_urlsafe(48))
    monkeypatch.setattr(settings, "ENABLE_CLINICIAN_FACT_REVIEW", True)


def request(version=None, **changes):
    return CreateClinicalFactReview.model_validate({
        "operation_id": uuid4(), "expected_version_id": version or uuid4(),
        "expected_review_id": None, "decision": "APPROVE", **changes,
    })


def event(payload, patient, actor, fact, **changes):
    return Event(
        id=uuid4(), patient_id=patient, reviewer_id=actor, fact_id=UUID(str(fact)),
        fact_version_id=payload.expected_version_id, fact_status="CONFIRMED",
        operation_id=payload.operation_id, previous_review_id=payload.expected_review_id,
        sequence=1, decision=payload.decision, reason=payload.reason,
        policy_version=service.POLICY_VERSION, recorded_at=datetime.now(UTC), **changes,
    )


@pytest.mark.parametrize("field", ["patient_id", "reviewer_id", "status", "fact", "approved"])
def test_client_cannot_supply_authority(field):
    with pytest.raises(ValidationError):
        request(**{field: "forged"})


@pytest.mark.parametrize("decision", ["REJECT", "WITHDRAW"])
@pytest.mark.parametrize("reason", [None, "", "   ", "x" * 2001])
def test_reason_required(decision, reason):
    with pytest.raises(ValidationError):
        request(decision=decision, reason=reason)


def test_expected_review_is_explicit_and_request_is_immutable():
    payload = request()
    data = payload.model_dump()
    del data["expected_review_id"]
    with pytest.raises(ValidationError):
        CreateClinicalFactReview.model_validate(data)
    with pytest.raises(ValidationError):
        payload.decision = "REJECT"


@pytest.mark.parametrize("previous", [None, "APPROVE", "REJECT", "WITHDRAW"])
@pytest.mark.parametrize("decision", ["APPROVE", "REJECT", "WITHDRAW"])
def test_transition_matrix(previous, decision):
    prior = SimpleNamespace(id=uuid4(), decision=previous) if previous else None
    payload = request(
        decision=decision,
        reason="synthetic review",
        expected_review_id=prior.id if prior else None,
    )
    if decision == "WITHDRAW" and previous != "APPROVE":
        with pytest.raises(HTTPException) as caught:
            service.validate_transition(prior, payload)
        assert caught.value.status_code == 409
    else:
        service.validate_transition(prior, payload)


def test_stale_predecessor_cannot_overwrite():
    with pytest.raises(HTTPException) as caught:
        service.validate_transition(SimpleNamespace(id=uuid4(), decision="APPROVE"), request())
    assert caught.value.status_code == 409


@pytest.fixture
def harness(monkeypatch):
    actor, patient, fact = uuid4(), uuid4(), uuid4()
    payload = request()
    db = SimpleNamespace(
        scalar=AsyncMock(), scalars=AsyncMock(), execute=AsyncMock(),
        add=Mock(), flush=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock(),
    )
    scope = AsyncMock(return_value=SimpleNamespace(id=actor, role="practitioner"))
    monkeypatch.setattr(service, "lock_patient_scope", scope)
    monkeypatch.setattr(service, "authorize_patient_scope", AsyncMock())
    validate = Mock()
    monkeypatch.setattr(service, "output", validate)
    target = SimpleNamespace(id=payload.expected_version_id, fact_id=fact)
    db.scalar.side_effect = [None, target, target, None]

    async def flush():
        db.add.call_args.args[0].recorded_at = datetime.now(UTC)

    db.flush.side_effect = flush
    return SimpleNamespace(
        actor=actor, patient=patient, fact=fact, payload=payload, db=db,
        scope=scope, target=target, validate=validate,
    )


async def call(h, payload=None):
    return await service.create_review(h.db, h.actor, h.patient, h.fact, payload or h.payload)


async def test_append_only_separate_event_and_scoped_queries(harness):
    h = harness
    result = await call(h)
    assert result.fact_version_id == h.payload.expected_version_id
    assert result.reviewer_id == h.actor and result.patient_id == h.patient
    assert result.sequence == 1 and result.previous_review_id is None
    h.db.commit.assert_awaited_once()
    h.validate.assert_called_once_with(h.target)
    h.db.add.assert_called_once()
    assert isinstance(h.db.add.call_args.args[0], Event)
    # The target and state queries are patient-scoped; event queries also bind actor.
    for index, args in enumerate(h.db.scalar.call_args_list):
        compiled = args.args[0].compile(dialect=postgresql.dialect())
        assert h.patient in compiled.params.values()
        if index in (0, 3):
            assert h.actor in compiled.params.values()
    assert "CONFIRMED" in h.db.scalar.call_args_list[1].args[0].compile().params.values()


@pytest.mark.parametrize("denial", [401, 403])
async def test_denial_precedes_all_review_queries(harness, denial):
    h = harness
    h.scope.side_effect = HTTPException(denial, "denied")
    with pytest.raises(HTTPException):
        await call(h)
    h.db.scalar.assert_not_awaited()
    h.db.add.assert_not_called()


async def test_patient_cannot_review_own_fact(harness):
    h = harness
    h.scope.return_value = SimpleNamespace(role="patient")
    with pytest.raises(HTTPException) as caught:
        await call(h)
    assert caught.value.status_code == 403
    h.db.scalar.assert_not_awaited()


@pytest.mark.parametrize("case", ["absent", "superseded", "review_changed"])
async def test_unavailable_or_stale_targets_do_not_write(harness, case):
    h = harness
    if case == "absent":
        h.db.scalar.side_effect = [None, None]
    elif case == "superseded":
        h.db.scalar.side_effect = [None, h.target, SimpleNamespace(id=uuid4())]
    else:
        h.db.scalar.side_effect = [None, h.target, h.target, SimpleNamespace(id=uuid4())]
    with pytest.raises(HTTPException) as caught:
        await call(h)
    assert caught.value.status_code == (404 if case == "absent" else 409)
    h.db.add.assert_not_called()


async def test_withdraw_old_version_does_not_modify_successor(harness):
    h = harness
    prior = event(h.payload, h.patient, h.actor, h.fact)
    payload = request(
        h.target.id, decision="WITHDRAW", reason="superseded", expected_review_id=prior.id
    )
    h.db.scalar.side_effect = [None, h.target, SimpleNamespace(id=uuid4()), prior]
    result = await call(h, payload)
    assert result.sequence == 2 and result.previous_review_id == prior.id
    assert result.decision == "WITHDRAW" and result.fact_version_id == h.target.id


async def test_exact_retry_returns_history_without_reactivation(harness):
    h = harness
    prior = event(h.payload, h.patient, h.actor, h.fact)
    h.db.scalar.side_effect = [prior]
    result = await call(h)
    assert result.id == prior.id
    h.db.add.assert_not_called()
    h.db.commit.assert_not_awaited()
    h.scope.assert_awaited_once()


@pytest.mark.parametrize(
    "field", ["decision", "reason", "expected_version_id", "expected_review_id"]
)
async def test_changed_retry_is_conflict(harness, field):
    h = harness
    prior = event(h.payload, h.patient, h.actor, h.fact)
    h.db.scalar.side_effect = [prior]
    data = h.payload.model_dump()
    data[field] = {"decision": "REJECT", "reason": "changed"}.get(field, uuid4())
    if field == "decision":
        data["reason"] = "changed"
    with pytest.raises(HTTPException) as caught:
        await call(h, CreateClinicalFactReview.model_validate(data))
    assert caught.value.status_code == 409
    h.db.add.assert_not_called()


@pytest.mark.parametrize("stage", ["flush", "commit"])
@pytest.mark.parametrize("error_type", [DBAPIError, OperationalError, InterfaceError])
@pytest.mark.parametrize("invalidated", [False, True])
async def test_storage_failure_is_unavailable_not_conflict(harness, stage, error_type, invalidated):
    h = harness
    getattr(h.db, stage).side_effect = error_type(
        "private SQL", {"private": "parameter"}, Exception("private detail"),
        connection_invalidated=invalidated,
    )
    with pytest.raises(HTTPException) as caught:
        await call(h)
    assert caught.value.status_code == 503
    assert caught.value.detail == "Review storage unavailable; retry with the same operation ID"
    assert caught.value.__suppress_context__ is True
    h.db.rollback.assert_awaited_once()
    h.db.add.assert_called_once()
    if stage == "flush":
        h.db.commit.assert_not_awaited()
    else:
        h.db.commit.assert_awaited_once()


@pytest.mark.parametrize("stage", ["flush", "commit"])
async def test_integrity_conflict_remains_409_and_rolls_back(harness, stage):
    h = harness
    getattr(h.db, stage).side_effect = IntegrityError(
        "private SQL", {"private": "parameter"}, Exception("private constraint detail")
    )
    with pytest.raises(HTTPException) as caught:
        await call(h)
    assert caught.value.status_code == 409
    assert caught.value.detail == "Review write conflict; reload before retrying"
    assert caught.value.__suppress_context__ is True
    h.db.rollback.assert_awaited_once()


@pytest.mark.parametrize("error_type, status", [(OperationalError, 503), (IntegrityError, 409)])
async def test_http_write_failure_returns_sanitized_status(harness, error_type, status):
    h = harness
    app = create_app()

    async def account():
        return SimpleNamespace(id=h.actor)

    async def database():
        yield h.db

    app.dependency_overrides[current_account] = account
    app.dependency_overrides[get_db] = database
    h.db.commit.side_effect = error_type(
        "private SQL", {"private": "parameter"}, Exception("private detail")
    )
    url = f"/api/v1/patients/{h.patient}/facts/{h.fact}/clinical-reviews"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(url, json=h.payload.model_dump(mode="json"))
    assert response.status_code == status
    assert "private" not in response.text
    if status == 503:
        assert "same operation ID" in response.json()["detail"]
    h.db.rollback.assert_awaited_once()


async def test_fresh_authorization_failure_rolls_back(harness, monkeypatch):
    h = harness
    monkeypatch.setattr(
        service, "authorize_patient_scope", AsyncMock(side_effect=HTTPException(403))
    )
    with pytest.raises(HTTPException):
        await call(h)
    h.db.flush.assert_not_awaited()
    h.db.rollback.assert_awaited_once()


async def test_current_status_is_exact_version_and_requesting_reviewer(harness):
    h = harness
    reviewed = event(h.payload, h.patient, h.actor, h.fact)
    other_version = SimpleNamespace(id=uuid4(), fact_id=uuid4())
    h.db.execute.return_value = [(h.target, reviewed), (other_version, None)]
    result = await service.current_reviews(h.db, h.actor, h.patient, limit=10, offset=0)
    assert [r.review_status for r in result] == ["APPROVED", "UNREVIEWED"]
    sql = h.db.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    assert h.patient in sql.params.values() and h.actor in sql.params.values()
    assert "clinical_fact_review_events.fact_version_id = clinical_fact_versions.id" in str(sql)
    h.db.add.assert_not_called()


async def test_history_filters_actor_patient_and_fact(harness):
    h = harness
    h.db.scalar.side_effect = [h.target]
    h.db.scalars.return_value = []
    assert await service.review_history(h.db, h.actor, h.patient, h.fact, limit=10, offset=0) == []
    params = h.db.scalars.call_args.args[0].compile().params.values()
    assert all(value in params for value in (h.actor, h.patient, h.fact))


@pytest.mark.parametrize("endpoint", ["clinical-fact-reviews", "facts/{fact}/clinical-reviews"])
async def test_http_requires_authentication(endpoint):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        url = f"/api/v1/patients/{uuid4()}/" + endpoint.format(fact=uuid4())
        assert (await client.get(url)).status_code == 401
        if "facts/" in endpoint:
            response = await client.post(url, json=request().model_dump(mode="json"))
            assert response.status_code == 401


async def test_http_review_uses_protected_response_and_private_validation(harness, monkeypatch):
    h = harness
    app = create_app()

    async def account():
        return SimpleNamespace(id=h.actor)

    async def database():
        yield h.db

    app.dependency_overrides[current_account] = account
    app.dependency_overrides[get_db] = database
    saved = service.event_output(event(h.payload, h.patient, h.actor, h.fact))
    create = AsyncMock(return_value=saved)
    monkeypatch.setattr(api_module, "create_review", create)
    from app.api.v1 import clinical_facts

    lock = AsyncMock()
    monkeypatch.setattr(clinical_facts, "lock_patient_scope", lock)
    url = f"/api/v1/patients/{h.patient}/facts/{h.fact}/clinical-reviews"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(url, json=h.payload.model_dump(mode="json"))
        assert response.status_code == 200 and response.json()["id"] == str(saved.id)
        assert response.headers["cache-control"] == "no-store"
        lock.assert_awaited_once_with(h.db, h.actor, h.patient)
        response = await client.post(
            url, json={**h.payload.model_dump(mode="json"), "reviewer_id": "private"}
        )
        assert response.status_code == 422 and "private" not in response.text
        create.assert_awaited_once()


async def test_http_revoked_acknowledgement_sends_no_event(harness, monkeypatch):
    from app.api.v1 import clinical_facts

    h = harness
    monkeypatch.setattr(
        clinical_facts, "lock_patient_scope", AsyncMock(side_effect=HTTPException(403))
    )
    response = clinical_facts.ClinicalResponse(
        h.db, h.actor, h.patient, service.event_output(event(h.payload, h.patient, h.actor, h.fact))
    )
    send = AsyncMock()
    with pytest.raises(HTTPException):
        await response({"type": "http"}, AsyncMock(), send)
    send.assert_not_awaited()
    h.db.rollback.assert_awaited_once()


def test_migration_no_backfill_append_only_and_guarded_downgrade(monkeypatch):
    revision = migration("0011_clinical_fact_reviews")
    recorder = Mock()
    monkeypatch.setattr(revision, "op", recorder)
    revision.upgrade()
    statements = [str(call.args[0]) for call in recorder.execute.call_args_list]
    assert any("BEFORE UPDATE OR DELETE" in sql for sql in statements)
    assert any("BEFORE TRUNCATE" in sql for sql in statements)
    assert not any(
        "INSERT INTO" in sql or "UPDATE clinical_fact_versions" in sql for sql in statements
    )
    recorder.reset_mock()
    revision.downgrade()
    assert "ACCESS EXCLUSIVE" in str(recorder.method_calls[0].args[0])
    assert "IF EXISTS" in str(recorder.method_calls[1].args[0])
    assert recorder.method_calls[2][0] == "drop_table"


# These tests use the existing dedicated-test-database fixture. The offline
# runner unsets TEST_DATABASE_URL and blocks connections, so they are skipped.
async def test_database_lifecycle_isolation_correction_and_revocation(db, api):
    patient, profile = await add_patient(db)
    doctor, practitioner = await add_practitioner(db)
    other, other_practitioner = await add_practitioner(db)
    await grant(api, patient, practitioner.id)
    await grant(api, patient, other_practitioner.id)
    fact = (await api.post(path(profile.id), headers=headers(patient), json=entry())).json()
    endpoint = f"{path(profile.id)}/{fact['fact_id']}/clinical-reviews"
    payload = request(fact["version_id"]).model_dump(mode="json")
    assert (await api.post(endpoint, headers=headers(patient), json=payload)).status_code == 403
    first = await api.post(endpoint, headers=headers(doctor), json=payload)
    assert first.status_code == 200
    assert (await api.post(endpoint, headers=headers(doctor), json=payload)).json() == first.json()
    listing = f"/api/v1/patients/{profile.id}/clinical-fact-reviews"
    other_status = (await api.get(listing, headers=headers(other))).json()[0]["review_status"]
    assert other_status == "UNREVIEWED"
    assert (await api.get(endpoint, headers=headers(other))).json() == []
    assert (await api.get(path(profile.id), headers=headers(patient))).json() == [fact]
    withdrawn = request(fact["version_id"], decision="WITHDRAW", reason="synthetic withdrawal",
                        expected_review_id=first.json()["id"]).model_dump(mode="json")
    response = await api.post(endpoint, headers=headers(doctor), json=withdrawn)
    assert response.status_code == 200
    status = (await api.get(listing, headers=headers(doctor))).json()[0]["review_status"]
    assert status == "WITHDRAWN"
    correction = await api.post(
        f"{path(profile.id)}/{fact['fact_id']}/corrections", headers=headers(patient),
        json={
            **entry("7.2"), "expected_version_id": fact["version_id"],
            "correction_reason": "synthetic",
        },
    )
    assert correction.status_code == 201
    status = (await api.get(listing, headers=headers(doctor))).json()[0]["review_status"]
    assert status == "UNREVIEWED"
    await revoke(api, patient, practitioner.id)
    assert (await api.get(listing, headers=headers(doctor))).status_code == 403
    assert (await api.post(endpoint, headers=headers(doctor), json=payload)).status_code == 403


@pytest.mark.parametrize("operation", ["update", "delete", "truncate"])
async def test_database_events_are_append_only(db, api, operation):
    patient, profile = await add_patient(db)
    doctor, practitioner = await add_practitioner(db)
    await grant(api, patient, practitioner.id)
    fact = (await api.post(path(profile.id), headers=headers(patient), json=entry())).json()
    response = await api.post(
        f"{path(profile.id)}/{fact['fact_id']}/clinical-reviews", headers=headers(doctor),
        json=request(fact["version_id"]).model_dump(mode="json"),
    )
    assert response.status_code == 200
    statement = {"update": update(Event).values(reason="changed"), "delete": delete(Event),
                 "truncate": text("TRUNCATE clinical_fact_review_events")}[operation]
    with pytest.raises(DBAPIError):
        async with db.begin_nested():
            await db.execute(statement)
    assert len(list(await db.scalars(select(Event)))) == 1


@pytest.mark.parametrize("same_operation", [True, False])
async def test_database_concurrent_reviews_have_one_owner(db, race_environment, same_operation):
    env = race_environment
    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        fact = (await client.get(path(env["patient_id"]), headers=env["doctor_auth"])).json()[0]
        url = f"{path(env['patient_id'])}/{fact['fact_id']}/clinical-reviews"
        payload = request(fact["version_id"]).model_dump(mode="json")
        second = payload if same_operation else request(fact["version_id"]).model_dump(mode="json")
        results = await asyncio.gather(
            client.post(url, headers=env["doctor_auth"], json=payload),
            client.post(url, headers=env["doctor_auth"], json=second),
        )
        expected = [200, 200] if same_operation else [200, 409]
        assert sorted(r.status_code for r in results) == expected
        history = (await client.get(url, headers=env["doctor_auth"])).json()
        assert len(history) == 1


async def test_database_committing_revocation_blocks_review(db, race_environment):
    env = race_environment
    env["hold_revoke"] = True
    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        fact = (await client.get(path(env["patient_id"]), headers=env["doctor_auth"])).json()[0]
        revocation = asyncio.create_task(client.post(
            f"/api/v1/patients/me/consents/{env['doctor_profile_id']}/revoke", headers=env["auth"]
        ))
        await asyncio.wait_for(env["commit_ready"].wait(), 3)
        review = asyncio.create_task(client.post(
            f"{path(env['patient_id'])}/{fact['fact_id']}/clinical-reviews",
            headers=env["doctor_auth"], json=request(fact["version_id"]).model_dump(mode="json"),
        ))
        try:
            await wait_until_blocked(db, env, "clinical")
        finally:
            env["commit_release"].set()
        revoked, denied = await asyncio.gather(revocation, review)
        assert revoked.status_code == 200 and denied.status_code == 403
    assert list(await db.scalars(select(Event))) == []


@pytest.mark.parametrize("case", ["patient", "version", "reviewer", "pending", "chain", "withdraw"])
async def test_database_constraints_reject_forged_review(db, api, case):
    from app.models import ClinicalFactVersion

    patient, profile = await add_patient(db)
    doctor, _ = await add_practitioner(db)
    fact = (await api.post(path(profile.id), headers=headers(patient), json=entry())).json()
    payload = request(fact["version_id"])
    candidate = event(payload, profile.id, doctor.id, fact["fact_id"])
    if case == "patient":
        _, foreign_profile = await add_patient(db)
        candidate.patient_id = foreign_profile.id
    elif case == "version":
        candidate.fact_version_id = uuid4()
    elif case == "reviewer":
        candidate.reviewer_id = patient.id
    elif case == "pending":
        original = await db.get(ClinicalFactVersion, payload.expected_version_id)
        pending_id = uuid4()
        db.add(ClinicalFactVersion(
            id=pending_id, fact_id=pending_id, patient_id=profile.id, version=1,
            attribution=original.attribution, author_id=patient.id, author_role="patient",
            payload=original.payload, provenance=original.provenance,
            observed_at=original.observed_at, entry_method="LLM_EXTRACTED", status="PENDING",
        ))
        await db.flush()
        candidate.fact_version_id = candidate.fact_id = pending_id
        candidate.fact_status = "PENDING"
    elif case == "chain":
        candidate.sequence = 2
    elif case == "withdraw":
        candidate.decision, candidate.reason = "WITHDRAW", "synthetic"
    with pytest.raises(DBAPIError):
        async with db.begin_nested():
            db.add(candidate)
            await db.flush()


async def test_database_cross_patient_api_never_exposes_reviews(db, api):
    patient, profile = await add_patient(db)
    stranger, foreign = await add_patient(db)
    doctor, practitioner = await add_practitioner(db)
    await grant(api, patient, practitioner.id)
    fact = (await api.post(path(foreign.id), headers=headers(stranger), json=entry())).json()
    for target, status in [(profile.id, 404), (foreign.id, 403)]:
        url = f"{path(target)}/{fact['fact_id']}/clinical-reviews"
        response = await api.post(
            url, headers=headers(doctor), json=request(fact["version_id"]).model_dump(mode="json")
        )
        assert response.status_code == status
        assert fact["version_id"] not in response.text


async def test_database_correction_race_never_transfers_approval(db, race_environment):
    env = race_environment
    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        fact = (await client.get(path(env["patient_id"]), headers=env["doctor_auth"])).json()[0]
        root = f"{path(env['patient_id'])}/{fact['fact_id']}"
        reviewed, corrected = await asyncio.gather(
            client.post(
                root + "/clinical-reviews", headers=env["doctor_auth"],
                json=request(fact["version_id"]).model_dump(mode="json"),
            ),
            client.post(
                root + "/corrections", headers=env["auth"],
                json={**entry("7.2"), "expected_version_id": fact["version_id"],
                      "correction_reason": "synthetic correction"},
            ),
        )
        assert reviewed.status_code in (200, 409) and corrected.status_code == 201
        listing = await client.get(
            f"/api/v1/patients/{env['patient_id']}/clinical-fact-reviews",
            headers=env["doctor_auth"],
        )
        assert listing.json()[0]["fact_version_id"] == corrected.json()["version_id"]
        assert listing.json()[0]["review_status"] == "UNREVIEWED"


async def run_revision(db, direction):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    revision = migration("0011_clinical_fact_reviews")

    def execute(connection):
        with Operations.context(MigrationContext.configure(connection)):
            getattr(revision, direction)()

    connection = await db.connection()
    await connection.run_sync(execute)


async def test_database_upgrade_preserves_existing_fact_and_no_implicit_approval(db, api):
    # db is strictly the existing opt-in random-schema test fixture, never the app DB.
    await run_revision(db, "downgrade")  # empty new table only
    await db.commit()
    patient, profile = await add_patient(db)
    doctor, practitioner = await add_practitioner(db)
    await grant(api, patient, practitioner.id)
    before = (await api.post(path(profile.id), headers=headers(patient), json=entry())).json()
    await run_revision(db, "upgrade")
    await db.commit()
    assert (await api.get(path(profile.id), headers=headers(patient))).json() == [before]
    response = await api.get(
        f"/api/v1/patients/{profile.id}/clinical-fact-reviews", headers=headers(doctor)
    )
    assert response.json()[0]["review_status"] == "UNREVIEWED"
    assert list(await db.scalars(select(Event))) == []


async def test_database_populated_downgrade_refuses_without_loss(db, api):
    patient, profile = await add_patient(db)
    doctor, practitioner = await add_practitioner(db)
    await grant(api, patient, practitioner.id)
    fact = (await api.post(path(profile.id), headers=headers(patient), json=entry())).json()
    response = await api.post(
        f"{path(profile.id)}/{fact['fact_id']}/clinical-reviews", headers=headers(doctor),
        json=request(fact["version_id"]).model_dump(mode="json"),
    )
    assert response.status_code == 200
    with pytest.raises(DBAPIError):
        async with db.begin_nested():
            await run_revision(db, "downgrade")
    assert len(list(await db.scalars(select(Event)))) == 1
    assert (await api.get(path(profile.id), headers=headers(patient))).json() == [fact]
