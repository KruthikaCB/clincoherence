"""Offline API/security tests; optional PostgreSQL tests use only the existing test fixture."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.api.v1.demo_registration import DemoRegistration, registration_database
from app.core.config import settings
from app.core.database import get_db
from app.models import PatientPractitionerConsent, PatientProfile, User
from app.provision_demo import IDENTITY

URL = "postgresql+asyncpg://synthetic:placeholder@localhost:5432/clincoherence_db"
BODY = {
    "identity": IDENTITY,
    "password": "demo1234",
    "password_confirmation": "demo1234",
    "acknowledge_unverified_demo": True,
}


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "ENABLE_DEMO_REGISTRATION", True)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "mock")
    monkeypatch.setattr(settings, "DATABASE_URL", URL)


@pytest.fixture
def mock_db():
    session = MagicMock()
    result = MagicMock()
    result.one.return_value = ("clincoherence_db", "::1", 5432)
    session.execute = AsyncMock(return_value=result)
    session.scalar = AsyncMock(return_value=None)
    session.flush = AsyncMock()
    return session


@pytest.fixture
async def registration_client(enabled, mock_db):
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    app = create_app()

    async def override():
        yield mock_db

    app.dependency_overrides[registration_database] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_create_only_demo_practitioner(registration_client, mock_db):
    response = await registration_client.post("/api/v1/auth/demo-registration", json=BODY)
    assert response.status_code == 201
    assert response.json() == {"status": "created", "identity_assurance": "unverified_demo"}
    assert response.headers["cache-control"] == "no-store"
    account, profile = [call.args[0] for call in mock_db.add.call_args_list]
    assert account.role == "practitioner" and account.is_active
    assert profile.account is account and profile.is_authorized
    assert account.hashed_password != BODY["password"]


@pytest.mark.parametrize(
    "setting,value",
    [
        ("APP_ENV", "production"),
        ("ENABLE_DEMO_REGISTRATION", False),
        ("LLM_PROVIDER", "external"),
        ("DATABASE_URL", URL.replace("localhost", "remote.invalid")),
        ("DATABASE_URL", URL.replace("clincoherence_db", "clincoherence_db_test")),
        ("DATABASE_URL", URL + "?host=remote.invalid"),
    ],
)
async def test_disabled(registration_client, mock_db, monkeypatch, setting, value):
    monkeypatch.setattr(settings, setting, value)
    for method in ["get", "post"]:
        response = await getattr(registration_client, method)(
            "/api/v1/auth/demo-registration", **({"json": BODY} if method == "post" else {})
        )
        assert response.status_code == 404
    mock_db.execute.assert_not_awaited()
    mock_db.add.assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [
        {"password": "seven77", "password_confirmation": "seven77"},
        {"password_confirmation": "different"},
        {"password": "x" * 1025},
        {"identity": "real@example.com"},
        {"role": "patient"},
        {"is_authorized": True},
        {"acknowledge_unverified_demo": False},
        {"patient_id": str(uuid4())},
    ],
)
async def test_invalid_private(registration_client, mock_db, changes):
    response = await registration_client.post(
        "/api/v1/auth/demo-registration", json={**BODY, **changes}
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid authentication or scope request"}
    mock_db.add.assert_not_called()


async def test_duplicate_never_updates(registration_client, mock_db):
    mock_db.scalar.return_value = uuid4()
    response = await registration_client.post("/api/v1/auth/demo-registration", json=BODY)
    assert response.status_code == 409
    mock_db.add.assert_not_called()
    mock_db.flush.assert_not_awaited()


async def test_foreign_browser_origin_denied(registration_client, mock_db):
    response = await registration_client.post(
        "/api/v1/auth/demo-registration", json=BODY, headers={"Origin": "https://remote.invalid"}
    )
    assert response.status_code == 404
    mock_db.add.assert_not_called()


async def test_unique_constraint_race_is_generic(registration_client, mock_db):
    from sqlalchemy.exc import IntegrityError

    mock_db.flush.side_effect = IntegrityError("SECRET_SENTINEL", {}, Exception("private"))
    response = await registration_client.post("/api/v1/auth/demo-registration", json=BODY)
    assert response.status_code == 409
    assert response.json() == {"detail": "Demo account cannot be created"}


async def test_remote_client_denied(enabled):
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    async with AsyncClient(
        transport=ASGITransport(app=create_app(), client=("192.0.2.1", 1234)),
        base_url="http://test",
    ) as client:
        assert (await client.get("/api/v1/auth/demo-registration")).status_code == 404


async def test_commit_failure_has_no_success_response(enabled, mock_db, monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.exc import OperationalError

    from app.main import create_app

    app = create_app()

    async def failed_commit():
        yield mock_db
        raise OperationalError("SECRET_SENTINEL", {}, Exception("private"))

    monkeypatch.setattr("app.api.v1.demo_registration.get_db", failed_commit)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/auth/demo-registration", json=BODY)
    assert response.status_code == 503
    assert response.json() == {"detail": "Demo registration could not be confirmed"}


async def test_wrong_connected_database(registration_client, mock_db):
    mock_db.execute.return_value.one.return_value = ("other", "::1", 5432)
    assert (
        await registration_client.post("/api/v1/auth/demo-registration", json=BODY)
    ).status_code == 409
    mock_db.add.assert_not_called()


def test_default_disabled():
    from app.core.config import Settings

    assert Settings.model_fields["ENABLE_DEMO_REGISTRATION"].default is False
    assert Settings.model_fields["APP_ENV"].default == "production"
    assert DemoRegistration(**BODY).password.get_secret_value() == "demo1234"


async def test_postgresql_registration_and_no_patient_access(api, db, enabled):
    # Existing fixture validates clincoherence_db_test; only override the local guard
    # and connected identity assertion for this isolated schema test.
    from unittest.mock import patch

    actual_execute = db.execute
    app = api._transport.app
    app.dependency_overrides[registration_database] = app.dependency_overrides[get_db]

    async def execute(statement, *args, **kwargs):
        if str(statement).startswith("SELECT current_database(), host("):
            result = MagicMock()
            result.one.return_value = ("clincoherence_db", "::1", 5432)
            return result
        return await actual_execute(statement, *args, **kwargs)

    with patch.object(db, "execute", side_effect=execute):
        created = await api.post("/api/v1/auth/demo-registration", json=BODY)
        assert created.status_code == 201
        assert (await api.post("/api/v1/auth/demo-registration", json=BODY)).status_code == 409
    assert await db.scalar(select(func.count()).select_from(User)) == 1
    assert await db.scalar(select(func.count()).select_from(PatientProfile)) == 0
    assert await db.scalar(select(func.count()).select_from(PatientPractitionerConsent)) == 0
    login = await api.post(
        "/api/v1/auth/login", json={"identity": IDENTITY, "password": "demo1234"}
    )
    assert login.status_code == 200
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    assert (await api.get(f"/api/v1/patients/{uuid4()}/scope", headers=headers)).status_code == 403
