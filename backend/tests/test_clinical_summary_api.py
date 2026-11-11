"""Summary HTTP bridge: real authentication/dependencies, unchanged audited handoff."""

import json
import secrets
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import audited_response as adapter
from app.api.dependencies import current_account, patient_scope
from app.api.v1.verified_records import release_session_factory
from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import create_app
from app.schemas.clinical_summary import ClinicalSummaryResponse
from app.services.clinical_summary import build_clinical_summary
from app.services.response_release import _Prepared, body_digest
from tests.test_clinical_facts import race_environment as race_environment
from tests.test_consistency import AUTHOR, PATIENT, fact
from tests.test_response_release import harness as harness
from tests.test_verified_records_api import database_bridge as database_bridge


@pytest.fixture(autouse=True)
def signing_configuration(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", secrets.token_urlsafe(48))


def path(patient=PATIENT):
    return f"/api/v1/patients/{patient}/clinical-summary"


def payload(patient=PATIENT):
    return {
        "operation_id": str(uuid4()),
        "request": {
            "patient_id": str(patient),
            "representation_version": "clinical-summary-json-v1",
        },
    }


@pytest.fixture
def bridge(monkeypatch, harness):
    app = create_app()
    app.dependency_overrides[current_account] = lambda: SimpleNamespace(id=AUTHOR)
    app.dependency_overrides[patient_scope] = lambda: PATIENT
    app.dependency_overrides[release_session_factory] = lambda: harness["factory"]
    body = (
        ClinicalSummaryResponse(summary=build_clinical_summary(PATIENT, [fact()], [fact()]))
        .model_dump_json()
        .encode()
    )
    receipt = harness["receipt"].model_copy(
        update={
            "representation_version": "clinical-summary-json-v1",
            "body_digest": body_digest(body),
        }
    )
    prepare = AsyncMock(return_value=_Prepared(receipt))
    revalidate = AsyncMock(return_value=(body, "a" * 64))
    monkeypatch.setattr(adapter, "prepare_summary_release", prepare)
    monkeypatch.setattr(adapter, "revalidate_summary_release", revalidate)
    return app, harness, prepare, revalidate, body


async def test_exact_audited_bytes_after_commit_in_distinct_sessions(bridge):
    app, h, preparing, _, body = bridge

    async def observed(scope, receive, send):
        async def checked(message):
            if message.get("status") == 200:
                assert h["trace"] == ["commit"] and len(h["sessions"]) == 2
            await send(message)

        await app(scope, receive, checked)

    async with AsyncClient(transport=ASGITransport(app=observed), base_url="http://test") as api:
        response = await api.post(path(), json=payload())
    assert response.status_code == 200 and response.content == body
    assert response.headers["cache-control"] == "no-store"
    assert preparing.await_args.args[1:3] == (AUTHOR, PATIENT)
    assert h["trace"] == ["commit", "rollback"]
    h["prepare"].assert_not_awaited()


@pytest.mark.parametrize("fault", ["patient", "scope", "forged", "version"])
async def test_invalid_selection_never_prepares(bridge, fault):
    app, _, prepare, _, _ = bridge
    body = payload()
    if fault == "patient":
        body["request"]["patient_id"] = str(uuid4())
    if fault == "scope":
        app.dependency_overrides[patient_scope] = lambda: uuid4()
    if fault == "forged":
        body["request"]["summary"] = "PRIVATE_UNRELEASED"
    if fault == "version":
        body["request"]["representation_version"] = "unknown"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
        response = await api.post(path(), json=body)
    assert response.status_code in (403, 422)
    assert "PRIVATE_UNRELEASED" not in response.text and "hba1c" not in response.text
    prepare.assert_not_awaited()


@pytest.mark.parametrize("fault", ["denied", "revoked", "stale", "commit"])
async def test_denial_or_release_failure_exposes_no_summary(bridge, fault):
    app, h, _, revalidate, body = bridge
    if fault == "denied":

        async def deny():
            raise HTTPException(403, "Patient scope denied")

        app.dependency_overrides[patient_scope] = deny
    elif fault == "commit":

        def failed_factory():
            db = h["factory"]()
            db.commit.side_effect = RuntimeError("PRIVATE_FAILURE")
            return db

        app.dependency_overrides[release_session_factory] = lambda: failed_factory
    else:
        revalidate.side_effect = HTTPException(
            403 if fault == "revoked" else 409, "PRIVATE_FAILURE"
        )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
        response = await api.post(path(), json=payload())
    assert response.status_code in (403, 409, 503) and response.content != body
    assert "PRIVATE_FAILURE" not in response.text and "hba1c" not in response.text


@pytest.mark.parametrize("kind", ["missing", "malformed", "expired"])
async def test_real_authentication_rejected_before_release(harness, kind):
    app = create_app()

    async def no_database():
        async with AsyncSession() as db:
            yield db

    app.dependency_overrides[get_db] = no_database
    token = create_access_token(str(AUTHOR), timedelta(seconds=-1))
    auth = (
        {}
        if kind == "missing"
        else {"Authorization": f"Bearer {token if kind == 'expired' else 'invalid'}"}
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
        response = await api.post(path(), headers=auth, json=payload())
    assert response.status_code == 401 and "summary" not in response.json()


@pytest.mark.parametrize("role", ["patient", "practitioner"])
async def test_postgresql_authenticated_patient_or_consented_practitioner(database_bridge, role):
    env, _, _, _ = database_bridge
    auth = env["auth"] if role == "patient" else env["doctor_auth"]
    async with AsyncClient(transport=ASGITransport(app=env["app"]), base_url="http://test") as api:
        response = await api.post(
            path(env["patient_id"]), headers=auth, json=payload(env["patient_id"])
        )
    assert response.status_code == 200
    assert response.json()["summary"]["patient_id"] == str(env["patient_id"])


@pytest.mark.parametrize("fault", ["foreign", "revoked"])
async def test_postgresql_scope_and_consent_denied(database_bridge, fault):
    env, _, _, _ = database_bridge
    patient = uuid4() if fault == "foreign" else env["patient_id"]
    async with AsyncClient(transport=ASGITransport(app=env["app"]), base_url="http://test") as api:
        if fault == "revoked":
            response = await api.post(
                f"/api/v1/patients/me/consents/{env['doctor_profile_id']}/revoke",
                headers=env["auth"],
            )
            assert response.status_code == 200
        response = await api.post(path(patient), headers=env["doctor_auth"], json=payload(patient))
    assert response.status_code == 403 and "summary" not in json.loads(response.content)
