"""Synthetic-only onboarding and single-use activation boundaries."""

import asyncio
import logging
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import Settings, settings
from app.core.database import get_db
from app.core.security import verify_password
from app.main import create_app
from app.models import PatientInvitation, PatientPractitionerConsent, PatientProfile, User
from app.schemas.invitation import CreateInvitation
from app.services.invitations import secret_digest
from tests.test_auth import add_patient, add_practitioner, headers

PATH = "/api/v1/patient-invitations"
PASSWORD = "demo1234"  # Exercise the accepted eight-character boundary.


@pytest.fixture(autouse=True)
def signing_configuration(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", secrets.token_urlsafe(48))


def test_hashes_are_fixed_size_and_not_raw_secrets():
    token, code = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    assert len(token) == 43
    assert len(secret_digest(token)) == 64
    assert secret_digest(token) != token
    assert secret_digest(token) != secret_digest(code)


def test_synthetic_identity_only():
    assert CreateInvitation(intended_identity="  USER01@SYNTHETIC.INVALID ").intended_identity == (
        "user01@synthetic.invalid"
    )
    with pytest.raises(ValidationError):
        CreateInvitation(intended_identity="someone@example.com")


@pytest.mark.parametrize(
    "origin",
    [
        "http://remote.example",
        "https://user:secret@example.com",
        "https://example.com/path",
        "https://example.com?token=secret",
        "javascript:alert(1)",
    ],
)
def test_unsafe_origin_rejected(origin):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, INVITATION_URL_ORIGIN=origin)


def test_invitation_configuration():
    assert (
        Settings(_env_file=None, INVITATION_URL_ORIGIN="https://example.com/").INVITATION_URL_ORIGIN
        == "https://example.com"
    )
    assert Settings(_env_file=None, INVITATION_EXPIRE_MINUTES=60).INVITATION_EXPIRE_MINUTES == 60
    with pytest.raises(ValidationError):
        Settings(_env_file=None, INVITATION_EXPIRE_MINUTES=0)


async def test_public_error_does_not_echo_secrets():
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as api:
        response = await api.post(
            PATH + "/activate", json={"token": "secret-input", "password": PASSWORD}
        )
        assert response.status_code == 422
        assert "secret-input" not in response.text and PASSWORD not in response.text
        response = await api.post(PATH + "/inspect", json={"token": "invalid"})
        assert response.status_code == 400 and "invalid" not in response.text


async def issue(api, issuer, identity=None):
    identity = identity or f"patient{uuid4().hex}@synthetic.invalid"
    response = await api.post(PATH, headers=headers(issuer), json={"intended_identity": identity})
    assert response.status_code == 201
    result = response.json()
    token = parse_qs(urlsplit(result["invitation_url"]).fragment)["token"][0]
    return result, {
        "token": token,
        "identity": identity,
        "verification_code": result["prototype_verification_code"],
        "password": PASSWORD,
    }


async def test_create_pending_profile_and_no_secrets_in_storage(db, api, caplog, monkeypatch):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(settings, "INVITATION_URL_ORIGIN", "http://localhost:4567")
    user, issuer = await add_practitioner(db)
    result, payload = await issue(api, user)
    invitation = await db.get(PatientInvitation, UUID(result["invitation_id"]))
    patient = await db.get(PatientProfile, UUID(result["patient_id"]))
    assert invitation.issued_by_id == issuer.id
    assert (
        patient.initiated_by_id == issuer.id
        and patient.account_id is None
        and not patient.is_active
    )
    assert invitation.token_hash == secret_digest(payload["token"])
    assert invitation.verification_hash == secret_digest(payload["verification_code"])
    assert "prototype-only" in result["prototype_notice"].lower()
    url = urlsplit(result["invitation_url"])
    assert url.netloc == "localhost:4567" and not url.query
    assert payload["verification_code"] not in result["invitation_url"]
    for secret in [payload["token"], payload["verification_code"], PASSWORD]:
        assert secret not in caplog.text
    assert await db.scalar(select(func.count()).select_from(PatientPractitionerConsent)) == 0


@pytest.mark.parametrize("role", ["unauthenticated", "patient", "inactive", "unauthorized"])
async def test_invitation_creation_denied(db, api, role):
    auth = {}
    if role == "patient":
        user, _ = await add_patient(db)
        auth = headers(user)
    elif role != "unauthenticated":
        user, _ = await add_practitioner(
            db, active=role != "inactive", authorized=role != "unauthorized"
        )
        auth = headers(user)
    response = await api.post(
        PATH, headers=auth, json={"intended_identity": "pending@synthetic.invalid"}
    )
    assert response.status_code == (403 if role == "patient" else 401)
    assert await db.scalar(select(func.count()).select_from(PatientInvitation)) == 0


async def test_inspect_activate_login_and_isolation(db, api):
    issuer, _ = await add_practitioner(db)
    result, payload = await issue(api, issuer)
    for _ in range(2):
        inspect = await api.post(PATH + "/inspect", json={"token": payload["token"]})
        assert inspect.status_code == 200
        assert set(inspect.json()) == {"valid", "expires_at", "prototype_notice"}
        assert inspect.headers["cache-control"] == "no-store"
    response = await api.post(PATH + "/activate", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["patient_id"] == result["patient_id"]
    account = await db.get(User, UUID(data["account_id"]))
    patient = await db.get(PatientProfile, UUID(data["patient_id"]))
    assert patient.is_active and patient.verified_at is not None and account.is_active
    assert verify_password(PASSWORD, account.hashed_password)
    assert await db.scalar(select(func.count()).select_from(PatientPractitionerConsent)) == 0
    assert (
        await api.get(f"/api/v1/patients/{patient.id}/scope", headers=headers(issuer))
    ).status_code == 403
    login = await api.post(
        "/api/v1/auth/login", json={"identity": payload["identity"], "password": PASSWORD}
    )
    assert login.status_code == 200
    auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await api.get(f"/api/v1/patients/{patient.id}/scope", headers=auth)).status_code == 200
    _, other_patient = await add_patient(db)
    assert (
        await api.get(f"/api/v1/patients/{other_patient.id}/scope", headers=auth)
    ).status_code == 403
    for path in ["/inspect", "/activate"]:
        assert (
            await api.post(
                PATH + path, json={"token": payload["token"]} if path == "/inspect" else payload
            )
        ).status_code == 400


@pytest.mark.parametrize("state", ["expired", "revoked", "invalid", "inactive_issuer"])
async def test_unavailable_invitations(db, api, state):
    issuer, _ = await add_practitioner(db)
    result, payload = await issue(api, issuer)
    invitation = await db.get(PatientInvitation, UUID(result["invitation_id"]))
    if state == "expired":
        invitation.created_at = datetime.now(UTC) - timedelta(days=2)
        invitation.expires_at = datetime.now(UTC) - timedelta(days=1)
        await db.commit()
    elif state == "revoked":
        assert (
            await api.post(f"{PATH}/{invitation.id}/revoke", headers=headers(issuer))
        ).status_code == 200
    elif state == "invalid":
        payload["token"] = secrets.token_urlsafe(32)
    else:
        issuer.is_active = False
        await db.commit()
    for path in ["/inspect", "/activate"]:
        response = await api.post(
            PATH + path, json={"token": payload["token"]} if path == "/inspect" else payload
        )
        assert response.status_code == 400
    patient = await db.get(PatientProfile, UUID(result["patient_id"]))
    assert patient.account_id is None and not patient.is_active


@pytest.mark.parametrize("case", ["wrong_code", "missing_code", "wrong_identity", "short_password"])
async def test_verification_required_without_consuming_invitation(db, api, case):
    issuer, _ = await add_practitioner(db)
    result, payload = await issue(api, issuer)
    if case == "wrong_code":
        payload["verification_code"] = secrets.token_urlsafe(24)
    elif case == "missing_code":
        del payload["verification_code"]
    elif case == "wrong_identity":
        payload["identity"] = "different@synthetic.invalid"
    else:
        payload["password"] = "short"
    response = await api.post(PATH + "/activate", json=payload)
    assert response.status_code == (422 if case == "missing_code" else 400)
    invitation = await db.get(PatientInvitation, UUID(result["invitation_id"]))
    assert invitation.status == "pending" and invitation.consumed_at is None


async def test_practitioner_cannot_manage_other_invitation(db, api):
    a, _ = await add_practitioner(db)
    b, _ = await add_practitioner(db)
    invitation, _ = await issue(api, a)
    own = await api.get(f"{PATH}/{invitation['invitation_id']}", headers=headers(a))
    assert own.status_code == 200
    assert set(own.json()) == {"invitation_id", "patient_id", "status", "expires_at"}
    for suffix in ["", "/revoke"]:
        path = f"{PATH}/{invitation['invitation_id']}{suffix}"
        response = await (
            api.post(path, headers=headers(b)) if suffix else api.get(path, headers=headers(b))
        )
        assert response.status_code == 404


async def test_token_and_verification_code_cannot_be_mixed_between_patients(db, api):
    issuer, _ = await add_practitioner(db)
    _, a = await issue(api, issuer)
    _, b = await issue(api, issuer)
    a["verification_code"] = b["verification_code"]
    assert (await api.post(PATH + "/activate", json=a)).status_code == 400
    assert (await api.post(PATH + "/activate", json=b)).status_code == 201


async def test_concurrent_activation_single_use(db, api):
    issuer, _ = await add_practitioner(db)
    _, payload = await issue(api, issuer)
    schema = await db.scalar(text("SELECT current_schema()"))
    # db fixture already requires TEST_DATABASE_URL; these independent sessions
    # use that same test engine URL and random schema, never the development URL.
    engine = create_async_engine(
        db.bind.engine.url, connect_args={"timeout": 5, "server_settings": {"search_path": schema}}
    )
    app = create_app()

    async def independent_db():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = independent_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            responses = await asyncio.gather(
                client.post(PATH + "/activate", json=payload),
                client.post(PATH + "/activate", json=payload),
            )
        assert sorted(r.status_code for r in responses) == [201, 400]
        assert (
            await db.scalar(
                select(func.count()).select_from(User).where(User.identity == payload["identity"])
            )
            == 1
        )
        assert await db.scalar(select(func.count()).select_from(PatientPractitionerConsent)) == 0
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "case",
    [
        "issuer_mismatch",
        "raw_token",
        "raw_code",
        "invalid_expiry",
        "invalid_lifecycle",
        "duplicate_hash",
    ],
)
async def test_invitation_constraints(db, api, case):
    issuer, _ = await add_practitioner(db)
    result, _ = await issue(api, issuer)
    invitation = await db.get(PatientInvitation, UUID(result["invitation_id"]))
    _, other = await add_practitioner(db)
    second, _ = await issue(api, issuer)
    second_invitation = await db.get(PatientInvitation, UUID(second["invitation_id"]))
    constraints = {
        "issuer_mismatch": "fk_invitation_patient_issuer",
        "raw_token": "ck_invitation_token_hash",
        "raw_code": "ck_invitation_verification_hash",
        "invalid_expiry": "ck_invitation_expiry",
        "invalid_lifecycle": "ck_invitation_lifecycle",
        "duplicate_hash": "patient_invitations_token_hash_key",
    }
    with pytest.raises(IntegrityError) as error:
        async with db.begin_nested():
            if case == "issuer_mismatch":
                invitation.issued_by_id = other.id
            elif case == "raw_token":
                invitation.token_hash = "not-a-digest"
            elif case == "raw_code":
                invitation.verification_hash = "not-a-digest"
            elif case == "invalid_expiry":
                invitation.expires_at = invitation.created_at - timedelta(minutes=1)
            elif case == "invalid_lifecycle":
                invitation.status = "consumed"
            else:
                second_invitation.token_hash = invitation.token_hash
            await db.flush()
    assert constraints[case] in str(error.value.orig)
