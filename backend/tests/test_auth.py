"""Authentication boundary tests and opt-in PostgreSQL authorization tests."""

import secrets
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from uuid import uuid4

import bcrypt
import pytest
from httpx import ASGITransport, AsyncClient
from jose import JWTError, jwt
from sqlalchemy import update

from app.core.config import settings
from app.core.database import get_db
from app.core.security import (
    create_access_token,
    decode_access_token,
    get_password_hash,
    verify_password,
)
from app.main import create_app
from app.models import PatientPractitionerConsent as Consent
from app.models import PatientProfile, PractitionerProfile, User

PASSWORD = "synthetic-password-only"
PREFIX = "/api/v1"


@pytest.fixture(autouse=True)
def test_signing_key(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", secrets.token_urlsafe(48))
    monkeypatch.setattr(settings, "ALGORITHM", "HS256")


@lru_cache(maxsize=1)
def password_hash():
    return get_password_hash(PASSWORD)


def test_password_hashing_and_legacy_verification():
    first, second = get_password_hash(PASSWORD), get_password_hash(PASSWORD)
    assert first != second and first != PASSWORD
    assert verify_password(PASSWORD, first)
    assert not verify_password("wrong", first)
    assert not verify_password(PASSWORD, "not-a-hash")
    legacy = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode()
    assert verify_password(PASSWORD, legacy)
    assert not verify_password(PASSWORD + "x", legacy)
    assert not verify_password("a" * 73, legacy)
    long_password = "a" * 100 + "suffix"
    assert verify_password(long_password, get_password_hash(long_password))


def test_jwt_round_trip():
    account_id = uuid4()
    assert decode_access_token(create_access_token(str(account_id))) == account_id


@pytest.mark.parametrize(
    "case",
    [
        "invalid",
        "expired",
        "forged",
        "missing_exp",
        "missing_sub",
        "wrong_subject",
        "wrong_audience",
        "wrong_issuer",
        "wrong_type",
        "wrong_algorithm",
        "missing_iat",
    ],
)
def test_reject_bad_tokens(case):
    now = datetime.now(UTC)
    claims = {
        "sub": str(uuid4()),
        "exp": now + timedelta(minutes=5),
        "iat": now,
        "iss": "clincoherence",
        "aud": "clincoherence-api",
        "token_type": "access",
    }
    key, algorithm = settings.SECRET_KEY, "HS256"
    if case == "expired":
        claims["exp"] = now - timedelta(minutes=1)
    elif case.startswith("missing_"):
        del claims[case.removeprefix("missing_")]
    elif case == "wrong_subject":
        claims["sub"] = "not-an-account-uuid"
    elif case == "wrong_audience":
        claims["aud"] = "other-api"
    elif case == "wrong_issuer":
        claims["iss"] = "other-issuer"
    elif case == "wrong_type":
        claims["token_type"] = "refresh"
    elif case == "wrong_algorithm":
        algorithm = "HS384"
    elif case == "forged":
        key = secrets.token_urlsafe(48)
    token = "not.a.token" if case == "invalid" else jwt.encode(claims, key, algorithm=algorithm)
    with pytest.raises((JWTError, ValueError)):
        decode_access_token(token)


@pytest.mark.parametrize("token", [None, "invalid.token.value"])
async def test_unauthenticated_http_without_database(token):
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response = await client.get(f"{PREFIX}/auth/me", headers=headers)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"


async def test_default_signing_key_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", "dev-secret-key-change-in-production")
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.post(
            f"{PREFIX}/auth/login", json={"identity": "synthetic@invalid", "password": PASSWORD}
        )
    assert response.status_code == 503
    assert PASSWORD not in response.text


async def test_validation_does_not_echo_password():
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.post(
            f"{PREFIX}/auth/login",
            json={"identity": 123, "password": {"submitted_secret": PASSWORD}},
        )
    assert response.status_code == 422
    assert PASSWORD not in response.text


@pytest.mark.parametrize("case", ["expired", "forged"])
async def test_invalid_signed_token_http(case):
    if case == "expired":
        token = create_access_token(str(uuid4()), timedelta(minutes=-1))
    else:
        claims = {
            "sub": str(uuid4()),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
            "iat": datetime.now(UTC),
            "iss": "clincoherence",
            "aud": "clincoherence-api",
            "token_type": "access",
        }
        token = jwt.encode(claims, secrets.token_urlsafe(48), algorithm="HS256")
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.get(
            f"{PREFIX}/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 401


@pytest.fixture
async def api(db):
    app = create_app()

    async def test_db():
        yield db

    app.dependency_overrides[get_db] = test_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def add_patient(db, *, verified=True, active=True, profile=True):
    user = User(
        identity=f"{uuid4().hex}@synthetic.invalid",
        hashed_password=password_hash(),
        role="patient",
        is_active=active,
    )
    db.add(user)
    await db.flush()
    patient = None
    if profile:
        now = datetime.now(UTC)
        patient = PatientProfile(
            account_id=user.id,
            is_active=verified,
            verified_at=now if verified else None,
            activated_at=now if verified else None,
        )
        db.add(patient)
        await db.flush()
    return user, patient


async def add_practitioner(db, *, authorized=True, active=True, profile=True):
    user = User(
        identity=f"{uuid4().hex}@synthetic.invalid",
        hashed_password=password_hash(),
        role="practitioner",
        is_active=active,
    )
    db.add(user)
    await db.flush()
    practitioner = None
    if profile:
        practitioner = PractitionerProfile(account_id=user.id, is_authorized=authorized)
        db.add(practitioner)
        await db.flush()
    return user, practitioner


async def login(api, user, password=PASSWORD):
    return await api.post(
        f"{PREFIX}/auth/login", json={"identity": user.identity, "password": password}
    )


def headers(user):
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


@pytest.mark.parametrize("role", ["patient", "practitioner"])
async def test_successful_login_and_me(db, api, role):
    user, _ = await (add_patient(db) if role == "patient" else add_practitioner(db))
    response = await login(api, user)
    assert response.status_code == 200
    assert set(response.json()) == {"access_token", "token_type"}
    assert response.headers["cache-control"] == "no-store"
    token = response.json()["access_token"]
    me = await api.get(f"{PREFIX}/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json() == {"id": str(user.id), "role": role}
    assert user.hashed_password not in me.text and PASSWORD not in me.text


@pytest.mark.parametrize(
    "case",
    [
        "wrong_password",
        "unknown_identity",
        "inactive_patient",
        "unverified_patient",
        "missing_patient_profile",
        "inactive_practitioner",
        "unauthorized_practitioner",
        "missing_practitioner_profile",
        "corrupt_hash",
    ],
)
async def test_login_denied(db, api, case):
    if "practitioner" in case:
        user, _ = await add_practitioner(
            db,
            active=case != "inactive_practitioner",
            authorized=case != "unauthorized_practitioner",
            profile=case != "missing_practitioner_profile",
        )
    else:
        user, _ = await add_patient(
            db,
            active=case != "inactive_patient",
            verified=case != "unverified_patient",
            profile=case != "missing_patient_profile",
        )
    if case == "unknown_identity":
        response = await api.post(
            f"{PREFIX}/auth/login",
            json={"identity": "not-in-db@synthetic.invalid", "password": PASSWORD},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid credentials or inactive account"
        return
    elif case == "corrupt_hash":
        user.hashed_password = "invalid-hash"
        await db.flush()
    response = await login(api, user, "wrong" if case == "wrong_password" else PASSWORD)
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials or inactive account"


async def test_patient_isolation(db, api):
    a, pa = await add_patient(db)
    _, pb = await add_patient(db)
    own = await api.get(f"{PREFIX}/patients/{pa.id}/scope", headers=headers(a))
    assert own.status_code == 200 and own.json() == {"patient_id": str(pa.id)}
    for patient_id in [pb.id, uuid4()]:
        response = await api.get(f"{PREFIX}/patients/{patient_id}/scope", headers=headers(a))
        assert response.status_code == 403


@pytest.mark.parametrize(
    "case",
    [
        "no_consent",
        "revoked",
        "wrong_patient",
        "wrong_practitioner",
        "future_grant",
        "inactive_patient",
        "pending_patient",
        "unauthorized_practitioner",
    ],
)
async def test_practitioner_denied(db, api, case):
    user, practitioner = await add_practitioner(db, authorized=case != "unauthorized_practitioner")
    patient_user, patient = await add_patient(db, verified=case != "pending_patient")
    if case == "inactive_patient":
        patient_user.is_active = False
    if case not in {"no_consent", "pending_patient"}:
        target, actor = patient, practitioner
        if case == "wrong_patient":
            _, target = await add_patient(db)
        if case == "wrong_practitioner":
            _, actor = await add_practitioner(db)
        now = datetime.now(UTC)
        db.add(
            Consent(
                patient_id=target.id,
                practitioner_id=actor.id,
                status="revoked" if case == "revoked" else "granted",
                granted_at=now + timedelta(days=1) if case == "future_grant" else now,
                revoked_at=now if case == "revoked" else None,
            )
        )
    await db.flush()
    response = await api.get(f"{PREFIX}/patients/{patient.id}/scope", headers=headers(user))
    assert response.status_code == (401 if case == "unauthorized_practitioner" else 403)


async def test_existing_jwt_denied_after_revocation(db, api):
    user, practitioner = await add_practitioner(db)
    _, patient = await add_patient(db)
    consent = Consent(
        patient_id=patient.id,
        practitioner_id=practitioner.id,
        status="granted",
        granted_at=datetime.now(UTC),
    )
    db.add(consent)
    await db.commit()
    token_response = await login(api, user)
    assert token_response.status_code == 200
    auth = {"Authorization": f"Bearer {token_response.json()['access_token']}"}
    path = f"{PREFIX}/patients/{patient.id}/scope"
    allowed = await api.get(path, headers=auth)
    assert allowed.status_code == 200
    assert allowed.json() == {"patient_id": str(patient.id)}
    # Update without synchronizing the previously loaded ORM consent object.
    # The endpoint must query current DB state rather than cached ORM/JWT claims.
    await db.execute(
        update(Consent)
        .where(Consent.id == consent.id)
        .values(status="revoked", revoked_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    assert consent.status == "granted"
    assert (await api.get(path, headers=auth)).status_code == 403


@pytest.mark.parametrize("case", ["inactive", "unauthorized", "missing_profile", "missing_account"])
async def test_existing_token_fails_current_account_checks(db, api, case):
    user, _ = await add_practitioner(
        db,
        active=case != "inactive",
        authorized=case != "unauthorized",
        profile=case != "missing_profile",
    )
    auth = headers(user)
    if case == "missing_account":
        auth = {"Authorization": f"Bearer {create_access_token(str(uuid4()))}"}
    response = await api.get(f"{PREFIX}/auth/me", headers=auth)
    assert response.status_code == 401
