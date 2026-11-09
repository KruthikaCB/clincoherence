"""HTTP bridge tests; dedicated PostgreSQL fixtures never use the development DB."""

import asyncio
import json
import secrets
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import audited_response as adapter
from app.api.dependencies import current_account, patient_scope
from app.api.v1.verified_records import release_session_factory
from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import create_app
from app.models import ClinicalFactVersion, ResponseReleaseIntent, VerificationAuditEvent
from app.services import response_release as release
from app.services.consistency import version_input
from tests.test_clinical_facts import entry, path
from tests.test_clinical_facts import race_environment as race_environment
from tests.test_consistency import AUTHOR, PATIENT
from tests.test_draft_verification import claim_for
from tests.test_evidence_aware_draft import composed
from tests.test_response_release import harness as harness


@pytest.fixture(autouse=True)
def signing_configuration(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", secrets.token_urlsafe(48))


def url(patient=PATIENT):
    return f"/api/v1/patients/{patient}/verified-records"


def envelope(request, operation=None):
    return {"operation_id": str(operation or uuid4()), "request": request.model_dump(mode="json")}


@pytest.fixture
def bridge(harness):
    app = create_app()
    app.dependency_overrides[current_account] = lambda: SimpleNamespace(id=AUTHOR)
    app.dependency_overrides[patient_scope] = lambda: PATIENT
    app.dependency_overrides[release_session_factory] = lambda: harness["factory"]
    return app, harness


async def test_http_returns_adapter_bytes_after_commit_using_distinct_sessions(bridge):
    app, h = bridge

    async def observed(scope, receive, send):
        async def checked(message):
            if message["type"] == "http.response.start":
                assert h["trace"] == ["commit"]
                assert len(h["sessions"]) == 2
                assert h["sessions"][0] is not h["sessions"][1]
            await send(message)

        await app(scope, receive, checked)

    async with AsyncClient(transport=ASGITransport(app=observed), base_url="http://test") as api:
        response = await api.post(url(), json=envelope(h["request"]))
    assert response.status_code == 200 and response.content == h["body"]
    assert response.headers["cache-control"] == "no-store"
    assert int(response.headers["content-length"]) == len(response.content)
    assert h["trace"] == ["commit", "rollback"]
    assert h["prepare"].await_args.args[1:3] == (AUTHOR, PATIENT)


@pytest.mark.parametrize("location", ["envelope", "request", "draft", "claim"])
@pytest.mark.parametrize(
    "field", ["PASS", "verified_text", "response_digest", "audit_id", "release_intent_id"]
)
async def test_forged_fields_rejected_without_echo_or_preparation(bridge, location, field):
    app, h = bridge
    body = envelope(h["request"])
    target = {
        "envelope": body,
        "request": body["request"],
        "draft": body["request"]["draft"],
        "claim": body["request"]["draft"]["claims"][0],
    }[location]
    target[field] = "SYNTHETIC_PRIVATE_VALUE"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
        result = await api.post(url(), json=body)
    assert result.status_code == 422
    assert "PRIVATE_VALUE" not in result.text and "records" not in result.text
    h["prepare"].assert_not_awaited()


async def test_path_request_mismatch_never_prepares(bridge):
    app, h = bridge
    body = envelope(h["request"])
    body["request"]["draft"]["patient_id"] = str(uuid4())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
        result = await api.post(url(), json=body)
    assert result.status_code == 409 and "records" not in result.text
    h["prepare"].assert_not_awaited()


@pytest.mark.parametrize("kind", ["missing", "malformed", "expired"])
async def test_authentication_rejected_before_release(harness, kind):
    app = create_app()

    async def no_database():
        async with AsyncSession() as db:
            yield db

    app.dependency_overrides[get_db] = no_database
    token = create_access_token(str(AUTHOR), timedelta(seconds=-10))
    headers = (
        {}
        if kind == "missing"
        else {"Authorization": f"Bearer {token if kind == 'expired' else 'invalid'}"}
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
        result = await api.post(url(), headers=headers, json=envelope(harness["request"]))
    assert result.status_code == 401 and "records" not in result.text
    harness["prepare"].assert_not_awaited()


@pytest.mark.parametrize("stage", ["prepare", "validate", "commit", "catalogue"])
async def test_http_failure_withholds_records(bridge, monkeypatch, stage):
    app, h = bridge
    if stage in ("prepare", "validate"):
        h[stage].side_effect = HTTPException(503, "SYNTHETIC_PRIVATE_VALUE")
    elif stage == "catalogue":
        monkeypatch.setattr(adapter, "catalogue_fingerprint", lambda: None)
    else:
        factory = h["factory"]

        def failing():
            db = factory()
            db.commit.side_effect = RuntimeError("SYNTHETIC_PRIVATE_VALUE")
            return db

        app.dependency_overrides[release_session_factory] = lambda: failing
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
        result = await api.post(url(), json=envelope(h["request"]))
    assert result.status_code == 503
    assert "records" not in result.text and "PRIVATE_VALUE" not in result.text


async def test_http_disconnect_before_commit_suppresses_protected_body(bridge):
    app, h = bridge
    body = json.dumps(envelope(h["request"])).encode()
    messages = []
    receives = 0

    async def receive():
        nonlocal receives
        receives += 1
        if receives == 1:
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": url(),
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "server": ("test", 80),
        "client": ("test", 123),
        "root_path": "",
    }
    with pytest.raises(asyncio.CancelledError):
        await app(scope, receive, AsyncMock(side_effect=messages.append))
    assert all(h["body"] != m.get("body") for m in messages)
    h["sessions"][0].commit.assert_not_awaited()


@pytest.fixture
async def database_bridge(db, race_environment):
    env = race_environment
    factory = async_sessionmaker(env["engine"], expire_on_commit=False)
    env["app"].dependency_overrides[release_session_factory] = lambda: factory
    row = version_input(await db.scalar(select(ClinicalFactVersion)))
    await db.rollback()
    return env, factory, row, envelope(composed(claim_for(row)))


@pytest.mark.parametrize("role", ["patient", "practitioner"])
async def test_database_authenticated_release_durable_before_http_and_no_replay(
    database_bridge, role
):
    env, factory, _, body = database_bridge

    async def observed(scope, receive, send):
        async def checked(message):
            if message["type"] == "http.response.start" and message["status"] == 200:
                async with factory() as observer:
                    assert (
                        await observer.scalar(
                            select(func.count()).select_from(ResponseReleaseIntent)
                        )
                        == 1
                    )
                    assert (
                        await observer.scalar(
                            select(func.count()).select_from(VerificationAuditEvent)
                        )
                        == 1
                    )
            await send(message)

        await env["app"](scope, receive, checked)

    auth = env["auth"] if role == "patient" else env["doctor_auth"]
    async with AsyncClient(transport=ASGITransport(app=observed), base_url="http://test") as api:
        result = await api.post(url(env["patient_id"]), headers=auth, json=body)
        assert result.status_code == 200 and result.json()["records"]
        async with factory() as observer:
            intent = await observer.scalar(select(ResponseReleaseIntent))
            assert release.body_digest(result.content) == intent.body_digest
        replay = await api.post(url(env["patient_id"]), headers=auth, json=body)
    assert replay.status_code == 409 and "records" not in replay.text


@pytest.mark.parametrize("change", ["revoke", "correct", "catalogue"])
async def test_database_changes_between_transactions_withhold(database_bridge, monkeypatch, change):
    env, _, row, body = database_bridge
    original = adapter.revalidate_release

    async def changed(*args):
        if change == "catalogue":
            monkeypatch.setattr(release, "catalogue_fingerprint", lambda: None)
        else:
            async with AsyncClient(
                transport=ASGITransport(app=env["app"]), base_url="http://test"
            ) as api:
                if change == "revoke":
                    result = await api.post(
                        f"/api/v1/patients/me/consents/{env['doctor_profile_id']}/revoke",
                        headers=env["auth"],
                    )
                else:
                    result = await api.post(
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
    async with AsyncClient(transport=ASGITransport(app=env["app"]), base_url="http://test") as api:
        result = await api.post(url(env["patient_id"]), headers=env["doctor_auth"], json=body)
    assert result.status_code == {"revoke": 403, "correct": 409, "catalogue": 503}[change]
    assert "records" not in result.text


@pytest.mark.parametrize("denial", ["revoked", "foreign_patient", "invalid_claim"])
async def test_database_denied_requests_never_release(database_bridge, denial):
    env, _, _, body = database_bridge
    patient = env["patient_id"]
    async with AsyncClient(transport=ASGITransport(app=env["app"]), base_url="http://test") as api:
        if denial == "revoked":
            assert (
                await api.post(
                    f"/api/v1/patients/me/consents/{env['doctor_profile_id']}/revoke",
                    headers=env["auth"],
                )
            ).status_code == 200
        elif denial == "foreign_patient":
            patient = uuid4()
            body["request"]["draft"]["patient_id"] = str(patient)
        else:
            body["request"]["draft"]["claims"][0]["proposed_content"] = "unverified synthetic prose"
        result = await api.post(url(patient), headers=env["doctor_auth"], json=body)
    assert result.status_code == (409 if denial == "invalid_claim" else 403)
    assert "records" not in result.text
