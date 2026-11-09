"""Minimal discovery responses and opt-in dedicated PostgreSQL policy coverage."""

import secrets
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.config import settings
from app.core.security import create_access_token
from app.main import create_app
from app.models import ConsentHistory, PatientInvitation, PatientProfile, User
from app.models import PatientPractitionerConsent as Consent
from app.schemas.patient_discovery import AccessiblePatient, OwnProfile
from app.services import patient_discovery as service
from tests.test_auth import add_patient, add_practitioner, headers

PROFILE = "/api/v1/auth/profile"
PATIENTS = "/api/v1/patients"


@pytest.fixture(autouse=True)
def signing_configuration(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", secrets.token_urlsafe(48))
    monkeypatch.setattr(settings, "ALGORITHM", "HS256")


@pytest.mark.parametrize("path", [PROFILE, PATIENTS])
@pytest.mark.parametrize("kind", ["missing", "invalid", "expired", "forged"])
async def test_authentication_failures_without_database(path, kind, monkeypatch):
    token = create_access_token(str(uuid4()), timedelta(seconds=-10 if kind == "expired" else 60))
    if kind == "forged":
        monkeypatch.setattr(settings, "SECRET_KEY", secrets.token_urlsafe(48))
    auth = (
        {}
        if kind == "missing"
        else {"Authorization": f"Bearer {'invalid' if kind == 'invalid' else token}"}
    )
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as api:
        result = await api.get(path, headers=auth)
    assert result.status_code == 401
    assert result.headers["www-authenticate"] == "Bearer"
    assert "profile_id" not in result.text and "patient_id" not in result.text


def test_schemas_are_minimal_immutable_and_extra_forbidden():
    own = OwnProfile(role="patient", profile_id=uuid4())
    item = AccessiblePatient(patient_id=uuid4())
    assert set(own.model_dump()) == {"role", "profile_id"}
    assert set(item.model_dump()) == {"patient_id"}
    for model in (own, item):
        with pytest.raises(ValidationError):
            type(model).model_validate({**model.model_dump(), "identity": "private"})
        with pytest.raises(ValidationError):
            model.patient_id = uuid4()


def test_routes_have_exact_paths_and_no_dynamic_patient_collision():
    paths = create_app().openapi()["paths"]
    for path in (
        PROFILE,
        PATIENTS,
        "/api/v1/patients/me/consents",
        "/api/v1/patients/{patient_id}/scope",
        "/api/v1/patients/{patient_id}/facts",
    ):
        assert "get" in paths[path]
    assert set(paths[PATIENTS]) == {"get"}


async def test_service_reuses_authoritative_scope_and_only_suppresses_denial(monkeypatch):
    actor, first, second = uuid4(), uuid4(), uuid4()
    db = AsyncMock()
    db.scalars.return_value = [first, second]
    monkeypatch.setattr(
        service, "eligible_account", AsyncMock(return_value=SimpleNamespace(role="practitioner"))
    )
    authorization = AsyncMock(side_effect=[HTTPException(403, "private"), second])
    monkeypatch.setattr(service, "authorize_patient_scope", authorization)
    assert await service.accessible_patients(db, actor) == [AccessiblePatient(patient_id=second)]
    authorization.assert_any_await(db, actor, first)
    authorization.assert_any_await(db, actor, second)
    authorization.side_effect = HTTPException(503, "unavailable")
    with pytest.raises(HTTPException) as error:
        await service.accessible_patients(db, actor)
    assert error.value.status_code == 503


@pytest.mark.parametrize("role", ["patient", "practitioner"])
async def test_own_profile_is_distinct_from_account_and_cannot_select_another(db, api, role):
    account, profile = await (add_patient(db) if role == "patient" else add_practitioner(db))
    other, other_profile = await (add_patient(db) if role == "patient" else add_practitioner(db))
    actor_id, profile_id, auth = account.id, profile.id, headers(account)
    other_id, other_profile_id = other.id, other_profile.id
    await db.commit()
    assert actor_id != profile_id
    result = await api.get(
        PROFILE,
        headers=auth,
        params={"account_id": str(other_id), "profile_id": str(other_profile_id)},
    )
    assert result.status_code == 200
    assert result.json() == {"role": role, "profile_id": str(profile_id)}
    assert result.headers["cache-control"] == "no-store"
    assert str(other_profile_id) not in result.text
    # Existing authentication contract remains unchanged.
    assert (await api.get("/api/v1/auth/me", headers=auth)).json() == {
        "id": str(actor_id),
        "role": role,
    }


@pytest.mark.parametrize(
    "case",
    [
        "patient_inactive",
        "patient_missing",
        "patient_pending",
        "patient_future",
        "practitioner_inactive",
        "practitioner_missing",
        "practitioner_unauthorized",
    ],
)
async def test_ineligible_identity_fails_closed_on_both_routes(db, api, case):
    if case.startswith("patient"):
        account, profile = await add_patient(
            db,
            active=case != "patient_inactive",
            profile=case != "patient_missing",
            verified=case != "patient_pending",
        )
        if case == "patient_future":
            profile.activated_at = datetime.now(UTC) + timedelta(days=1)
    else:
        account, _ = await add_practitioner(
            db,
            active=case != "practitioner_inactive",
            profile=case != "practitioner_missing",
            authorized=case != "practitioner_unauthorized",
        )
    auth = headers(account)
    await db.commit()
    for path in (PROFILE, PATIENTS):
        result = await api.get(path, headers=auth)
        assert result.status_code == 401
        assert "profile_id" not in result.text and "patient_id" not in result.text


async def test_patient_denied_practitioner_list_and_practitioner_empty_list(db, api):
    patient, _ = await add_patient(db)
    doctor, _ = await add_practitioner(db)
    patient_auth, doctor_auth = headers(patient), headers(doctor)
    await db.commit()
    assert (await api.get(PATIENTS, headers=patient_auth)).status_code == 403
    result = await api.get(PATIENTS, headers=doctor_auth)
    assert result.status_code == 200 and result.json() == []
    assert result.headers["cache-control"] == "no-store"


async def test_pair_isolation_ordering_revocation_and_existing_token(db, api):
    a, pa = await add_patient(db)
    b, pb = await add_patient(db)
    _, unrelated = await add_patient(db)
    one, p_one = await add_practitioner(db)
    two, p_two = await add_practitioner(db)
    one_auth, two_auth = headers(one), headers(two)
    a_auth, b_auth = headers(a), headers(b)
    pa_id, pb_id, unrelated_id = pa.id, pb.id, unrelated.id
    one_id, two_id = p_one.id, p_two.id
    await db.commit()
    for auth, target in ((a_auth, one_id), (b_auth, one_id), (b_auth, two_id)):
        assert (
            await api.post(
                "/api/v1/patients/me/consents", headers=auth, json={"practitioner_id": str(target)}
            )
        ).status_code == 200
    expected = [{"patient_id": value} for value in sorted([str(pa_id), str(pb_id)])]
    for _ in range(2):
        result = await api.get(PATIENTS, headers=one_auth, params={"practitioner_id": str(two_id)})
        assert result.json() == expected and result.headers["cache-control"] == "no-store"
        assert str(unrelated_id) not in result.text
        assert all(set(row) == {"patient_id"} for row in result.json())
    assert (await api.get(PATIENTS, headers=two_auth)).json() == [{"patient_id": str(pb_id)}]
    assert (
        await api.post(f"/api/v1/patients/me/consents/{one_id}/revoke", headers=b_auth)
    ).status_code == 200
    assert (await api.get(PATIENTS, headers=one_auth)).json() == [{"patient_id": str(pa_id)}]
    assert (await api.get(f"/api/v1/patients/{pb_id}/scope", headers=one_auth)).status_code == 403
    assert (await api.get(PATIENTS, headers=two_auth)).json() == [{"patient_id": str(pb_id)}]


@pytest.mark.parametrize("case", ["inactive_account", "future_activation", "future_grant"])
async def test_ineligible_patient_relationships_excluded(db, api, case):
    patient, profile = await add_patient(db)
    doctor, practitioner = await add_practitioner(db)
    now = datetime.now(UTC)
    db.add(
        Consent(
            patient_id=profile.id,
            practitioner_id=practitioner.id,
            status="granted",
            granted_at=now + timedelta(days=1) if case == "future_grant" else now,
        )
    )
    if case == "inactive_account":
        patient.is_active = False
    if case == "future_activation":
        profile.activated_at = now + timedelta(days=1)
    auth = headers(doctor)
    await db.commit()
    result = await api.get(PATIENTS, headers=auth)
    assert result.status_code == 200 and result.json() == []


async def test_invitation_and_activation_never_make_patient_discoverable_without_consent(db, api):
    doctor, _ = await add_practitioner(db)
    auth = headers(doctor)
    await db.commit()
    identity = f"{uuid4().hex}@synthetic.invalid"
    created = await api.post(
        "/api/v1/patient-invitations", headers=auth, json={"intended_identity": identity}
    )
    assert created.status_code == 201
    data = created.json()
    assert (await api.get(PATIENTS, headers=auth)).json() == []
    token = parse_qs(urlsplit(data["invitation_url"]).fragment)["token"][0]
    activation = await api.post(
        "/api/v1/patient-invitations/activate",
        json={
            "token": token,
            "identity": identity,
            "verification_code": data["prototype_verification_code"],
            "password": "synthetic-password-only",
        },
    )
    assert activation.status_code == 201
    assert (await api.get(PATIENTS, headers=auth)).json() == []
    assert (
        await api.get(f"/api/v1/patients/{data['patient_id']}/scope", headers=auth)
    ).status_code == 403
    assert await db.scalar(select(func.count()).select_from(Consent)) == 0


async def test_discovery_does_not_mutate_relationships_or_history(db, api):
    patient, _ = await add_patient(db)
    doctor, practitioner = await add_practitioner(db)
    doctor_auth, patient_auth = headers(doctor), headers(patient)
    practitioner_id = practitioner.id
    await db.commit()
    assert (
        await api.post(
            "/api/v1/patients/me/consents",
            headers=patient_auth,
            json={"practitioner_id": str(practitioner_id)},
        )
    ).status_code == 200
    models = (User, PatientProfile, PatientInvitation, Consent, ConsentHistory)
    before = [await db.scalar(select(func.count()).select_from(model)) for model in models]
    history_before = list((await db.execute(select(ConsentHistory))).scalars())
    snapshot = [(row.id, row.status, row.version, row.recorded_at) for row in history_before]
    for auth in (doctor_auth, patient_auth):
        assert (await api.get(PROFILE, headers=auth)).status_code == 200
    assert (await api.get(PATIENTS, headers=doctor_auth)).status_code == 200
    assert before == [await db.scalar(select(func.count()).select_from(model)) for model in models]
    history_after = list(
        (
            await db.execute(select(ConsentHistory).execution_options(populate_existing=True))
        ).scalars()
    )
    assert snapshot == [(row.id, row.status, row.version, row.recorded_at) for row in history_after]
