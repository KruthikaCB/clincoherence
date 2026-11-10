"""Offline local gates and migration checks; opt-in dedicated PostgreSQL ownership tests."""

from io import StringIO
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError

from app.api.v1 import demo_patients as endpoint
from app.api.v1.demo_registration import registration_database
from app.core import demo_patients as guard
from app.core.config import settings
from app.core.database import get_db
from app.models import PatientPractitionerConsent, PatientProfile, User
from tests.test_auth import add_patient, add_practitioner, headers
from tests.test_clinical_facts import entry

URL = "postgresql+asyncpg://synthetic:placeholder@localhost:5432/clincoherence_db"


@pytest.fixture
def enabled(monkeypatch):
    for name, value in {
        "APP_ENV": "development",
        "ENABLE_DEMO_PATIENTS": True,
        "LLM_PROVIDER": "mock",
        "DATABASE_URL": URL,
        "SECRET_KEY": "synthetic-test-signing-key-" * 3,
    }.items():
        monkeypatch.setattr(settings, name, value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("APP_ENV", "production"),
        ("ENABLE_DEMO_PATIENTS", False),
        ("LLM_PROVIDER", "external"),
        ("DATABASE_URL", URL.replace("localhost", "remote.invalid")),
        ("DATABASE_URL", URL.replace("clincoherence_db", "clincoherence_db_test")),
        ("DATABASE_URL", URL + "?host=remote.invalid"),
    ],
)
def test_disabled_target(enabled, monkeypatch, field, value):
    monkeypatch.setattr(settings, field, value)
    assert not guard.enabled()


def test_default_disabled_and_valid_opt_in(enabled):
    assert settings.model_fields["ENABLE_DEMO_PATIENTS"].default is False
    assert guard.enabled()


@pytest.mark.parametrize(
    "row",
    [
        ("clincoherence_db", "::1", 5432),
        ("clincoherence_db_test", "::1", 5432),
        ("clincoherence_db", "192.0.2.1", 5432),
        ("clincoherence_db", "::1", 5433),
    ],
)
async def test_connected_database_guard(enabled, row):
    db = MagicMock()
    result = MagicMock()
    result.one.return_value = row
    db.execute = AsyncMock(return_value=result)
    assert await guard.verified_database(db) is (row == ("clincoherence_db", "::1", 5432))


@pytest.mark.parametrize(
    "payload",
    [
        {"identifier": "real name"},
        {"identifier": "x", "owner_id": str(uuid4())},
        {"identifier": "x", "is_active": True},
        {"identifier": ""},
    ],
)
def test_only_synthetic_identifier_input(payload):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        endpoint.CreateDemoPatient(**payload)


async def test_disabled_endpoint_and_authentication(enabled, monkeypatch):
    from app.main import create_app

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        for method in ("get", "post"):
            response = await getattr(client, method)(
                "/api/v1/demo-patients",
                **({"json": {"identifier": "demo-1"}} if method == "post" else {}),
            )
            assert response.status_code == 401
        monkeypatch.setattr(settings, "ENABLE_DEMO_PATIENTS", False)
        assert (await client.get("/api/v1/demo-patients")).status_code == 404


def test_migration_0009_sql_preserves_existing_rows():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from tests.database import migration

    module = migration("0009_demo_patients")
    assert module.down_revision == "0008_evidence_release"
    buffer = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": buffer}
    )
    with Operations.context(context):
        module.upgrade()
    sql = buffer.getvalue()
    assert "ADD COLUMN is_demo BOOLEAN DEFAULT false NOT NULL" in sql
    assert "IS DISTINCT FROM OLD.demo_owner_id" in sql
    assert "IS DISTINCT FROM OLD.is_demo" in sql
    assert "REFERENCES practitioner_profiles (id) ON DELETE RESTRICT" in sql
    assert "DELETE FROM" not in sql and "UPDATE patient_profiles" not in sql
    assert "DROP TABLE" not in sql


@pytest.fixture
def isolated_demo_api(api, enabled, monkeypatch):
    # The fixture has independently verified clincoherence_db_test. Only the
    # development-only database identity guard is substituted, never authorization.
    for module in (guard, endpoint):
        monkeypatch.setattr(module, "verified_database", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "app.services.authorization.verified_database", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "app.services.patient_discovery.verified_database", AsyncMock(return_value=True)
    )
    app = api._transport.app
    app.dependency_overrides[registration_database] = app.dependency_overrides[get_db]
    return api


async def test_postgresql_creator_scope_and_ordinary_consent(db, isolated_demo_api, monkeypatch):
    from app.services.authorization import authorize_patient_scope

    api = isolated_demo_api
    owner, _ = await add_practitioner(db)
    other, _ = await add_practitioner(db)
    created = await api.post(
        "/api/v1/demo-patients", headers=headers(owner), json={"identifier": "demo-1"}
    )
    assert created.status_code == 201
    assert created.json()["label"] == "Synthetic Demo Patient"
    patient_id = UUID(created.json()["patient_id"])
    patient = await db.get(PatientProfile, patient_id)
    assert patient.is_demo and patient.account_id is None
    assert patient.activated_at is None and patient.verified_at is None
    assert await db.scalar(select(func.count()).select_from(User)) == 2
    assert await db.scalar(select(func.count()).select_from(PatientPractitionerConsent)) == 0
    assert (await api.get("/api/v1/patients", headers=headers(owner))).json() == [
        {"patient_id": str(patient_id)}
    ]
    assert (await api.get("/api/v1/patients", headers=headers(other))).json() == []
    assert (await api.get("/api/v1/demo-patients", headers=headers(other))).json() == []
    await authorize_patient_scope(db, owner.id, patient_id)
    with pytest.raises(HTTPException):
        await authorize_patient_scope(db, other.id, patient_id)
    _, ordinary = await add_patient(db)
    with pytest.raises(HTTPException):
        await authorize_patient_scope(db, owner.id, ordinary.id)
    response = await api.post(
        f"/api/v1/patients/{patient_id}/facts", headers=headers(owner), json=entry("7.1000000000")
    )
    assert response.status_code == 201
    assert response.json()["attribution"] == "practitioner_managed"
    assert (
        await api.get(f"/api/v1/patients/{patient_id}/facts", headers=headers(other))
    ).status_code == 403
    from app.schemas.record_questions import RecordQuestion
    from app.services.record_questions import prepare_question
    from app.services.response_release import prepare_release, revalidate_release

    operation = uuid4()
    request = await prepare_question(
        db,
        owner.id,
        patient_id,
        RecordQuestion(operation_id=operation, question="What is my latest recorded hba1c?"),
    )
    prepared = await prepare_release(db, owner.id, patient_id, request, operation)
    assert prepared.intent is not None
    assert b"7.1" in (await revalidate_release(db, owner.id, patient_id, request, prepared))[0]
    monkeypatch.setattr(
        "app.services.authorization.verified_database", AsyncMock(return_value=False)
    )
    with pytest.raises(HTTPException):
        await revalidate_release(db, owner.id, patient_id, request, prepared)


@pytest.mark.parametrize("column", ["is_demo", "demo_owner_id", "demo_identifier"])
async def test_postgresql_immutable_ownership(db, isolated_demo_api, column):
    owner, _ = await add_practitioner(db)
    response = await isolated_demo_api.post(
        "/api/v1/demo-patients", headers=headers(owner), json={"identifier": "demo-immutable"}
    )
    patient_id = UUID(response.json()["patient_id"])
    value = {"is_demo": False, "demo_owner_id": uuid4(), "demo_identifier": "changed"}[column]
    async with db.begin_nested():
        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(
                    update(PatientProfile)
                    .where(PatientProfile.id == patient_id)
                    .values({column: value})
                )
    assert (await db.get(PatientProfile, patient_id)).is_demo


async def test_postgresql_duplicate_and_patient_denial(db, isolated_demo_api):
    owner, _ = await add_practitioner(db)
    patient_user, _ = await add_patient(db)
    assert (
        await isolated_demo_api.post(
            "/api/v1/demo-patients", headers=headers(patient_user), json={"identifier": "demo-1"}
        )
    ).status_code == 403
    assert (
        await isolated_demo_api.post(
            "/api/v1/demo-patients", headers=headers(owner), json={"identifier": "demo-1"}
        )
    ).status_code == 201
    async with db.begin_nested() as savepoint:
        result = await isolated_demo_api.post(
            "/api/v1/demo-patients", headers=headers(owner), json={"identifier": "demo-1"}
        )
        assert result.status_code == 409
        await savepoint.rollback()
    assert (
        await db.scalar(
            select(func.count()).select_from(PatientProfile).where(PatientProfile.is_demo.is_(True))
        )
        == 1
    )
