"""Structured facts, immutable corrections, and revocation ordering."""

import asyncio
import secrets
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, Request
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.v1 import clinical_facts as endpoints
from app.api.v1.clinical_facts import ClinicalResponse
from app.core.config import settings
from app.core.database import get_db
from app.main import create_app
from app.models import ClinicalFactVersion
from app.schemas.clinical_fact import CreateFact
from tests.test_auth import add_patient, add_practitioner, headers
from tests.test_consents import grant, revoke


def path(patient_id):
    return f"/api/v1/patients/{patient_id}/facts"


def entry(value="7.1"):
    return {
        "fact": {"kind": "laboratory", "code": "hba1c", "value": value, "unit": "%"},
        "observed_at": "2026-01-01T10:00:00Z",
        "provenance": {
            "source": "synthetic fixture",
            "reference": "fixture-1",
            "data_origin": "synthetic",
        },
    }


@pytest.fixture(autouse=True)
def signing_configuration(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", secrets.token_urlsafe(48))


@pytest.mark.parametrize(
    "fact",
    [
        {"kind": "diagnosis", "code": "type_2_diabetes"},
        {
            "kind": "medication",
            "medication_name": "recorded metformin",
            "dose_value": "500",
            "dose_unit": "mg",
        },
        {"kind": "laboratory", "code": "glucose", "value": "100.25", "unit": "mg/dL"},
        {"kind": "symptom", "code": "fatigue", "description": "synthetic report"},
        {
            "kind": "history",
            "category": "family_history",
            "description": "synthetic family history",
        },
    ],
)
def test_typed_vocabulary(fact):
    payload = entry()
    payload["fact"] = fact
    assert CreateFact.model_validate(payload).fact.kind == fact["kind"]


@pytest.mark.parametrize(
    "case",
    [
        "author",
        "role",
        "patient_id",
        "status",
        "entry_method",
        "confirmed_by_id",
        "bad_unit",
        "nan",
        "naive_time",
        "real_data",
        "dose_missing_unit",
    ],
)
def test_structural_validation_and_server_assigned_fields(case):
    payload = entry()
    if case == "bad_unit":
        payload["fact"]["unit"] = "mg/dL"
    elif case == "nan":
        payload["fact"]["value"] = "NaN"
    elif case == "naive_time":
        payload["observed_at"] = "2026-01-01T10:00:00"
    elif case == "real_data":
        payload["provenance"]["data_origin"] = "identifiable_real_patient"
    elif case == "dose_missing_unit":
        payload["fact"] = {
            "kind": "medication",
            "medication_name": "synthetic medication",
            "dose_value": "1",
        }
    else:
        payload[case] = "client-selected"
    with pytest.raises(ValidationError):
        CreateFact.model_validate(payload)


async def test_response_gate_retained_through_body_handoff(monkeypatch):
    held = False
    sent = []
    db = AsyncMock()

    async def acquire(*args):
        nonlocal held
        held = True

    async def commit():
        nonlocal held
        assert sent[-1] == "http.response.body"
        held = False

    async def send(message):
        assert held
        sent.append(message["type"])

    monkeypatch.setattr(endpoints, "lock_patient_scope", acquire)
    db.commit.side_effect = commit
    response = ClinicalResponse(db, uuid4(), uuid4(), {"synthetic": "body"})
    await response({"type": "http"}, AsyncMock(), send)
    assert not held and sent == ["http.response.start", "http.response.body"]


async def test_denied_release_sends_no_clinical_bytes(monkeypatch):
    async def denied(*args):
        raise HTTPException(403, "Patient scope denied")

    monkeypatch.setattr(endpoints, "lock_patient_scope", denied)
    db, send = AsyncMock(), AsyncMock()
    with pytest.raises(HTTPException):
        await ClinicalResponse(db, uuid4(), uuid4(), {"synthetic": "body"})(
            {"type": "http"}, AsyncMock(), send
        )
    send.assert_not_awaited()
    db.rollback.assert_awaited_once()


async def test_creation_attribution_isolation_and_current_consent(db, api):
    patient, profile = await add_patient(db)
    other, other_profile = await add_patient(db)
    doctor, practitioner = await add_practitioner(db)
    patient_auth, other_auth, doctor_auth = headers(patient), headers(other), headers(doctor)
    patient_id, other_id, doctor_id = profile.id, other_profile.id, doctor.id
    response = await api.post(path(patient_id), json=entry(), headers=patient_auth)
    assert response.status_code == 201
    first = response.json()
    assert first["attribution"] == "patient_reported" and first["author_role"] == "patient"
    assert first["status"] == "CONFIRMED" and first["entry_method"] == "STRUCTURED"
    assert first["confirmed_by_id"] is None and first["confirmed_at"] is None
    assert first["observed_at"] != first["entered_at"]
    assert (await api.get(path(patient_id), headers=patient_auth)).json() == [first]
    assert (await api.get(path(patient_id), headers=other_auth)).status_code == 403
    assert (await api.post(path(other_id), json=entry(), headers=patient_auth)).status_code == 403
    assert (await api.get(path(patient_id), headers=doctor_auth)).status_code == 403
    # Retain IDs/headers across denied requests; do not rely on expired ORM objects.
    await db.refresh(patient)
    assert (await grant(api, patient, practitioner.id)).status_code == 200
    result = await api.post(path(patient_id), json=entry("7.2"), headers=doctor_auth)
    assert result.status_code == 201
    assert result.json()["attribution"] == "practitioner_managed"
    assert result.json()["author_id"] == str(doctor_id)
    await db.refresh(patient)
    assert (await revoke(api, patient, practitioner.id)).status_code == 200
    assert (await api.get(path(patient_id), headers=doctor_auth)).status_code == 403
    assert (await api.post(path(patient_id), json=entry(), headers=doctor_auth)).status_code == 403


async def test_corrections_preserve_history_and_reject_stale_or_cross_category(db, api):
    patient, profile = await add_patient(db)
    doctor, practitioner = await add_practitioner(db)
    assert (await grant(api, patient, practitioner.id)).status_code == 200
    auth, doctor_auth, patient_id = headers(patient), headers(doctor), profile.id
    first = (await api.post(path(patient_id), json=entry(), headers=auth)).json()
    correction = {
        **entry("7.3"),
        "expected_version_id": first["version_id"],
        "correction_reason": "Corrected synthetic transcription",
    }
    correction_path = f"{path(patient_id)}/{first['fact_id']}/corrections"
    response = await api.post(correction_path, json=correction, headers=auth)
    assert response.status_code == 201
    second = response.json()
    assert second["version"] == 2 and second["previous_version_id"] == first["version_id"]
    assert second["fact_id"] == first["fact_id"] and second["provenance"] == first["provenance"]
    history = await api.get(f"{path(patient_id)}/{first['fact_id']}/history", headers=auth)
    assert history.json() == [first, second]
    assert (await api.get(path(patient_id), headers=auth)).json() == [second]
    assert (await api.post(correction_path, json=correction, headers=auth)).status_code == 409
    correction["expected_version_id"] = second["version_id"]
    assert (
        await api.post(correction_path, json=correction, headers=doctor_auth)
    ).status_code == 403
    # Practitioner-managed facts have the reciprocal write boundary.
    clinical = (await api.post(path(patient_id), json=entry(), headers=doctor_auth)).json()
    correction["expected_version_id"] = clinical["version_id"]
    assert (
        await api.post(
            f"{path(patient_id)}/{clinical['fact_id']}/corrections", json=correction, headers=auth
        )
    ).status_code == 403


async def test_cross_patient_fact_ids_not_exposed(db, api):
    a, pa = await add_patient(db)
    b, pb = await add_patient(db)
    auth_a, auth_b, id_a, id_b = headers(a), headers(b), pa.id, pb.id
    fact = (await api.post(path(id_b), headers=auth_b, json=entry())).json()
    assert (
        await api.get(f"{path(id_a)}/{fact['fact_id']}/history", headers=auth_a)
    ).status_code == 404
    correction = {
        **entry(),
        "expected_version_id": fact["version_id"],
        "correction_reason": "synthetic correction",
    }
    assert (
        await api.post(
            f"{path(id_a)}/{fact['fact_id']}/corrections", headers=auth_a, json=correction
        )
    ).status_code == 404


def candidate(patient_id, author_id, *, status="PENDING", previous=None):
    version_id = uuid4()
    payload = CreateFact.model_validate(entry())
    return ClinicalFactVersion(
        id=version_id,
        fact_id=previous.fact_id if previous else version_id,
        patient_id=patient_id,
        version=previous.version + 1 if previous else 1,
        previous_version_id=previous.id if previous else None,
        previous_version=previous.version if previous else None,
        correction_reason="proposed synthetic correction" if previous else None,
        attribution="patient_reported",
        author_id=author_id,
        author_role="patient",
        payload=payload.fact.model_dump(mode="json"),
        provenance=payload.provenance.model_dump(mode="json"),
        observed_at=payload.observed_at,
        entry_method="LLM_EXTRACTED",
        status=status,
    )


async def test_pending_rejected_facts_never_replace_accepted_state(db, api):
    patient, profile = await add_patient(db)
    auth, patient_id, account_id = headers(patient), profile.id, patient.id
    accepted = (await api.post(path(patient_id), headers=auth, json=entry())).json()
    original = await db.get(ClinicalFactVersion, UUID(accepted["version_id"]))
    db.add_all(
        [
            candidate(patient_id, account_id),
            candidate(patient_id, account_id, status="REJECTED"),
            candidate(patient_id, account_id, previous=original),
        ]
    )
    await db.commit()
    assert (await api.get(path(patient_id), headers=auth)).json() == [accepted]
    assert (
        await api.get(f"{path(patient_id)}/{accepted['fact_id']}/history", headers=auth)
    ).json() == [accepted]
    correction = {
        **entry("7.4"),
        "expected_version_id": accepted["version_id"],
        "correction_reason": "explicit structured correction",
    }
    result = await api.post(
        f"{path(patient_id)}/{accepted['fact_id']}/corrections", headers=auth, json=correction
    )
    assert result.status_code == 201 and result.json()["version"] == 3
    assert result.json()["previous_version_id"] == accepted["version_id"]


@pytest.mark.parametrize(
    "case", ["cross_patient", "null_previous_number", "unconfirmed_llm", "wrong_confirmer"]
)
async def test_database_chain_and_acceptance_constraints(db, api, case):
    patient, profile = await add_patient(db)
    other, other_profile = await add_patient(db)
    original_data = (
        await api.post(path(profile.id), headers=headers(patient), json=entry())
    ).json()
    original = await db.get(ClinicalFactVersion, UUID(original_data["version_id"]))
    row = candidate(profile.id, patient.id, previous=original)
    if case == "cross_patient":
        row.patient_id = other_profile.id
    elif case == "null_previous_number":
        row.previous_version = None
    else:
        row.status = "CONFIRMED"
        if case == "wrong_confirmer":
            row.confirmed_by_id, row.confirmed_at = other.id, datetime.now(UTC)
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(row)
            await db.flush()


@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_versions_are_append_only(db, api, operation):
    patient, profile = await add_patient(db)
    data = (await api.post(path(profile.id), headers=headers(patient), json=entry())).json()
    row_id = UUID(data["version_id"])
    statement = (
        update(ClinicalFactVersion).where(ClinicalFactVersion.id == row_id).values(payload={})
        if operation == "update"
        else delete(ClinicalFactVersion).where(ClinicalFactVersion.id == row_id)
    )
    with pytest.raises(DBAPIError) as error:
        async with db.begin_nested():
            await db.execute(statement.execution_options(synchronize_session=False))
    assert "Clinical fact versions are append-only" in str(error.value.orig)


@pytest.fixture
async def race_environment(db, api):
    patient, profile = await add_patient(db)
    doctor, practitioner = await add_practitioner(db)
    assert (await grant(api, patient, practitioner.id)).status_code == 200
    auth, doctor_auth = headers(patient), headers(doctor)
    patient_id, doctor_profile_id = profile.id, practitioner.id
    assert (await api.post(path(patient_id), headers=auth, json=entry())).status_code == 201
    schema = await db.scalar(text("SELECT current_schema()"))
    engine = create_async_engine(
        db.bind.engine.url,
        hide_parameters=True,
        connect_args={"timeout": 5, "server_settings": {"search_path": schema}},
    )
    app = create_app()
    pids = {}
    commit_ready, commit_release = asyncio.Event(), asyncio.Event()
    hold_revoke = False

    async def independent_db(request: Request):
        async with AsyncSession(engine, expire_on_commit=False) as session:
            pid = await session.scalar(text("SELECT pg_backend_pid()"))
            pids["revoke" if request.url.path.endswith("/revoke") else "clinical"] = pid
            if request.url.path.endswith("/revoke") and env["hold_revoke"]:
                original_commit = session.commit

                async def delayed_commit():
                    commit_ready.set()
                    await commit_release.wait()
                    await original_commit()

                session.commit = delayed_commit
            yield session

    env = {
        "app": app,
        "engine": engine,
        "patient_id": patient_id,
        "doctor_profile_id": doctor_profile_id,
        "auth": auth,
        "doctor_auth": doctor_auth,
        "pids": pids,
        "hold_revoke": hold_revoke,
        "commit_ready": commit_ready,
        "commit_release": commit_release,
    }
    app.dependency_overrides[get_db] = independent_db
    try:
        yield env
    finally:
        commit_release.set()
        await engine.dispose()


async def wait_until_blocked(db, env, key):
    for _ in range(200):
        pid = env["pids"].get(key)
        if pid and await db.scalar(
            text("SELECT cardinality(pg_blocking_pids(:pid))"), {"pid": pid}
        ):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Expected PostgreSQL row-lock wait was not observed")


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_committed_revocation_prevents_waiting_read_or_write(db, race_environment, method):
    env = race_environment
    env["hold_revoke"] = True
    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        revocation = asyncio.create_task(
            client.post(
                f"/api/v1/patients/me/consents/{env['doctor_profile_id']}/revoke",
                headers=env["auth"],
            )
        )
        await asyncio.wait_for(env["commit_ready"].wait(), 3)
        clinical = asyncio.create_task(
            client.request(
                method,
                path(env["patient_id"]),
                headers=env["doctor_auth"],
                **({"json": entry()} if method == "POST" else {}),
            )
        )
        try:
            await wait_until_blocked(db, env, "clinical")
        finally:
            env["commit_release"].set()
        revoked, denied = await asyncio.gather(revocation, clinical)
        assert revoked.status_code == 200 and denied.status_code == 403
    assert (
        await db.scalar(
            select(func.count())
            .select_from(ClinicalFactVersion)
            .where(ClinicalFactVersion.patient_id == env["patient_id"])
        )
        == 1
    )


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_clinical_handoff_finishes_before_revocation_can_commit(db, race_environment, method):
    env = race_environment
    ready, release, handed_off = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def gated_app(scope, receive, send):
        async def gated_send(message):
            if message["type"] == "http.response.body":
                ready.set()
                await release.wait()
                await send(message)
                handed_off.set()
            else:
                await send(message)

        await env["app"](scope, receive, gated_send)

    async with (
        AsyncClient(
            transport=ASGITransport(app=gated_app), base_url="http://test"
        ) as clinical_client,
        AsyncClient(transport=ASGITransport(app=env["app"]), base_url="http://test") as client,
    ):
        clinical = asyncio.create_task(
            clinical_client.request(
                method,
                path(env["patient_id"]),
                headers=env["doctor_auth"],
                **({"json": entry()} if method == "POST" else {}),
            )
        )
        await asyncio.wait_for(ready.wait(), 3)
        revocation = asyncio.create_task(
            client.post(
                f"/api/v1/patients/me/consents/{env['doctor_profile_id']}/revoke",
                headers=env["auth"],
            )
        )
        try:
            await wait_until_blocked(db, env, "revoke")
            assert not handed_off.is_set()
        finally:
            release.set()
        result, revoked = await asyncio.gather(clinical, revocation)
        assert result.status_code == (201 if method == "POST" else 200)
        assert handed_off.is_set() and revoked.status_code == 200
        assert (
            await client.get(path(env["patient_id"]), headers=env["doctor_auth"])
        ).status_code == 403


async def test_revocation_in_write_acknowledgement_gap_suppresses_body(
    db, race_environment, monkeypatch
):
    env = race_environment
    committed, resume = asyncio.Event(), asyncio.Event()
    original = endpoints.structured_entry

    async def pause_after_commit(*args, **kwargs):
        result = await original(*args, **kwargs)
        committed.set()
        await resume.wait()
        return result

    monkeypatch.setattr(endpoints, "structured_entry", pause_after_commit)
    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        writing = asyncio.create_task(
            client.post(path(env["patient_id"]), headers=env["doctor_auth"], json=entry())
        )
        await asyncio.wait_for(committed.wait(), 3)
        try:
            revoked = await client.post(
                f"/api/v1/patients/me/consents/{env['doctor_profile_id']}/revoke",
                headers=env["auth"],
            )
            assert revoked.status_code == 200
        finally:
            resume.set()
        denied = await writing
        assert denied.status_code == 403 and "hba1c" not in denied.text
    assert (
        await db.scalar(
            select(func.count())
            .select_from(ClinicalFactVersion)
            .where(ClinicalFactVersion.patient_id == env["patient_id"])
        )
        == 2
    )
