"""Own-patient consent lifecycle, history, and concurrent mutation boundaries."""

import asyncio
import secrets
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import settings
from app.core.database import get_db
from app.main import create_app
from app.models import ConsentHistory
from app.models import PatientPractitionerConsent as Consent
from app.schemas.consent import GrantConsent
from tests.database import migration
from tests.test_auth import add_patient, add_practitioner, headers

PATH = "/api/v1/patients/me/consents"


@pytest.fixture(autouse=True)
def signing_configuration(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", secrets.token_urlsafe(48))


def test_grant_cannot_select_patient_or_actor():
    for extra in ["patient_id", "account_id", "actor_id", "status"]:
        with pytest.raises(ValidationError):
            GrantConsent.model_validate({"practitioner_id": str(uuid4()), extra: str(uuid4())})
    assert GrantConsent(practitioner_id=uuid4()).practitioner_id is not None


async def test_unauthenticated_consent_operations_denied():
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as api:
        assert (await api.get(PATH)).status_code == 401
        assert (await api.post(PATH, json={"practitioner_id": str(uuid4())})).status_code == 401
        assert (await api.post(f"{PATH}/{uuid4()}/revoke")).status_code == 401


async def grant(api, patient, practitioner_id):
    return await api.post(
        PATH, headers=headers(patient), json={"practitioner_id": str(practitioner_id)}
    )


async def revoke(api, patient, practitioner_id):
    return await api.post(f"{PATH}/{practitioner_id}/revoke", headers=headers(patient))


async def history(db, consent_id):
    return list(
        await db.scalars(
            select(ConsentHistory)
            .where(ConsentHistory.consent_id == UUID(consent_id))
            .order_by(ConsentHistory.version)
        )
    )


async def test_explicit_grant_and_own_listing(db, api):
    patient, profile = await add_patient(db)
    practitioner, doctor = await add_practitioner(db)
    path = f"/api/v1/patients/{profile.id}/scope"
    assert (await api.get(path, headers=headers(practitioner))).status_code == 403
    assert (await api.get(PATH, headers=headers(patient))).json() == []
    result = await grant(api, patient, doctor.id)
    assert result.status_code == 200 and result.json()["status"] == "granted"
    assert set(result.json()) == {
        "consent_id",
        "practitioner_id",
        "status",
        "granted_at",
        "revoked_at",
    }
    listed = await api.get(PATH, headers=headers(patient))
    assert listed.status_code == 200 and listed.json() == [result.json()]
    assert listed.headers["cache-control"] == "no-store"
    assert (await api.get(path, headers=headers(practitioner))).status_code == 200
    events = await history(db, result.json()["consent_id"])
    assert len(events) == 1 and events[0].kind == "granted" and events[0].actor_id == patient.id


async def test_patients_cannot_manage_or_list_other_patient_consent(db, api):
    a, pa = await add_patient(db)
    b, pb = await add_patient(db)
    _, doctor = await add_practitioner(db)
    consent_b = await grant(api, b, doctor.id)
    assert (await api.get(PATH, headers=headers(a), params={"patient_id": str(pb.id)})).json() == []
    assert (await revoke(api, a, doctor.id)).status_code == 404
    injection = await api.post(
        PATH, headers=headers(a), json={"practitioner_id": str(doctor.id), "patient_id": str(pb.id)}
    )
    assert injection.status_code == 422
    own = await grant(api, a, doctor.id)
    assert own.status_code == 200
    assert own.json()["consent_id"] != consent_b.json()["consent_id"]
    assert (await revoke(api, a, doctor.id)).status_code == 200
    assert (await api.get(PATH, headers=headers(b))).json() == [consent_b.json()]
    assert (
        await db.scalar(
            select(func.count()).select_from(Consent).where(Consent.patient_id == pa.id)
        )
        == 1
    )


async def test_practitioner_cannot_manage_patient_consent(db, api):
    patient, _ = await add_patient(db)
    practitioner, doctor = await add_practitioner(db)
    result = await grant(api, patient, doctor.id)
    assert result.status_code == 200
    assert (await api.get(PATH, headers=headers(practitioner))).status_code == 403
    assert (await grant(api, practitioner, doctor.id)).status_code == 403
    assert (await revoke(api, practitioner, doctor.id)).status_code == 403
    assert (await api.get(PATH, headers=headers(patient))).json() == [result.json()]


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "inactive",
        "unauthorized",
        "missing_profile",
        "patient_role",
        "account_id_instead_of_profile",
    ],
)
async def test_ineligible_practitioner_denied(db, api, case):
    patient, _ = await add_patient(db)
    target_id = uuid4()
    if case == "patient_role":
        _, target = await add_patient(db)
        target_id = target.id
    elif case != "missing":
        user, profile = await add_practitioner(
            db,
            active=case != "inactive",
            authorized=case != "unauthorized",
            profile=case != "missing_profile",
        )
        target_id = (
            user.id if case in {"missing_profile", "account_id_instead_of_profile"} else profile.id
        )
    response = await grant(api, patient, target_id)
    assert response.status_code == 403
    assert await db.scalar(select(func.count()).select_from(Consent)) == 0
    assert await db.scalar(select(func.count()).select_from(ConsentHistory)) == 0


@pytest.mark.parametrize("case", ["inactive", "unverified", "missing_profile"])
async def test_ineligible_patient_cannot_manage_consent(db, api, case):
    patient, _ = await add_patient(
        db,
        active=case != "inactive",
        verified=case != "unverified",
        profile=case != "missing_profile",
    )
    _, doctor = await add_practitioner(db)
    assert (await api.get(PATH, headers=headers(patient))).status_code == 401
    assert (await grant(api, patient, doctor.id)).status_code == 401
    assert (await revoke(api, patient, doctor.id)).status_code == 401


async def test_multiple_practitioners_existing_jwt_revocation(db, api):
    patient, profile = await add_patient(db)
    p1, d1 = await add_practitioner(db)
    p2, d2 = await add_practitioner(db)
    jwt1, jwt2 = headers(p1), headers(p2)
    path = f"/api/v1/patients/{profile.id}/scope"
    assert (await grant(api, patient, d1.id)).status_code == 200
    assert (await grant(api, patient, d2.id)).status_code == 200
    assert (await api.get(path, headers=jwt1)).status_code == 200
    assert (await api.get(path, headers=jwt2)).status_code == 200
    assert (await revoke(api, patient, d1.id)).status_code == 200
    assert (await api.get(path, headers=jwt1)).status_code == 403
    assert (await api.get(path, headers=jwt2)).status_code == 200


async def test_repeated_operations_and_regrant_preserve_history(db, api):
    patient, _ = await add_patient(db)
    _, doctor = await add_practitioner(db)
    assert (await revoke(api, patient, doctor.id)).status_code == 404
    assert await db.scalar(select(func.count()).select_from(Consent)) == 0
    first = (await grant(api, patient, doctor.id)).json()
    assert (await grant(api, patient, doctor.id)).json() == first
    assert len(await history(db, first["consent_id"])) == 1
    second = (await revoke(api, patient, doctor.id)).json()
    assert second["granted_at"] == first["granted_at"]
    assert second["revoked_at"] is not None
    assert (await revoke(api, patient, doctor.id)).json() == second
    assert len(await history(db, first["consent_id"])) == 2
    third = (await grant(api, patient, doctor.id)).json()
    assert third["consent_id"] == first["consent_id"]
    assert third["status"] == "granted" and third["revoked_at"] is None
    assert third["granted_at"] > second["revoked_at"]
    events = await history(db, first["consent_id"])
    assert [e.version for e in events] == [1, 2, 3]
    assert [e.kind for e in events] == ["granted", "revoked", "granted"]
    assert all(e.actor_id == patient.id for e in events)
    assert events[0].granted_at == events[1].granted_at
    assert events[1].revoked_at is not None and events[2].revoked_at is None
    assert await db.scalar(select(func.count()).select_from(Consent)) == 1


async def test_revoke_allowed_after_practitioner_becomes_ineligible(db, api):
    patient, _ = await add_patient(db)
    practitioner, doctor = await add_practitioner(db)
    initial = await grant(api, patient, doctor.id)
    practitioner.is_active = False
    doctor.is_authorized = False
    await db.commit()
    result = await revoke(api, patient, doctor.id)
    assert result.status_code == 200 and result.json()["status"] == "revoked"
    assert (await grant(api, patient, doctor.id)).status_code == 403
    assert len(await history(db, initial.json()["consent_id"])) == 2


async def test_legacy_snapshot_before_change(db, api):
    patient, profile = await add_patient(db)
    _, doctor = await add_practitioner(db)
    original = datetime.now(UTC)
    consent = Consent(
        patient_id=profile.id, practitioner_id=doctor.id, status="granted", granted_at=original
    )
    db.add(consent)
    await db.commit()
    assert (await revoke(api, patient, doctor.id)).status_code == 200
    events = await history(db, str(consent.id))
    assert [e.kind for e in events] == ["baseline", "revoked"]
    assert events[0].actor_id is None and events[0].granted_at == original
    assert events[1].actor_id == patient.id


@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_history_is_append_only(db, api, operation):
    patient, _ = await add_patient(db)
    _, doctor = await add_practitioner(db)
    result = await grant(api, patient, doctor.id)
    event = (await history(db, result.json()["consent_id"]))[0]
    statement = (
        update(ConsentHistory).where(ConsentHistory.id == event.id).values(kind="baseline")
        if operation == "update"
        else delete(ConsentHistory).where(ConsentHistory.id == event.id)
    )
    with pytest.raises(DBAPIError) as error:
        async with db.begin_nested():
            await db.execute(statement.execution_options(synchronize_session=False))
    assert "Consent history is append-only" in str(error.value.orig)
    assert len(await history(db, result.json()["consent_id"])) == 1


async def test_direct_current_state_history_mismatch_fails_closed(db, api):
    patient, _ = await add_patient(db)
    _, doctor = await add_practitioner(db)
    result = await grant(api, patient, doctor.id)
    await db.execute(
        update(Consent)
        .where(Consent.id == UUID(result.json()["consent_id"]))
        .values(status="revoked", revoked_at=datetime.now(UTC))
    )
    await db.commit()
    assert (await grant(api, patient, doctor.id)).status_code == 409
    assert len(await history(db, result.json()["consent_id"])) == 1


@pytest.mark.parametrize("case", ["two_grants", "grant_and_revoke"])
async def test_concurrent_consent_operations(db, api, case):
    patient, profile = await add_patient(db)
    _, doctor = await add_practitioner(db)
    if case == "grant_and_revoke":
        assert (await grant(api, patient, doctor.id)).status_code == 200
    await db.commit()
    schema = await db.scalar(text("SELECT current_schema()"))
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
                grant(client, patient, doctor.id),
                grant(client, patient, doctor.id)
                if case == "two_grants"
                else revoke(client, patient, doctor.id),
            )
        assert [r.status_code for r in responses] == [200, 200]
        rows = list(
            await db.scalars(
                select(Consent)
                .where(Consent.patient_id == profile.id)
                .execution_options(populate_existing=True)
            )
        )
        assert len(rows) == 1
        consent = rows[0]
        events = await history(db, str(consent.id))
        assert [e.version for e in events] == list(range(1, len(events) + 1))
        assert events[-1].status == consent.status
        assert (
            events[-1].granted_at == consent.granted_at
            and events[-1].revoked_at == consent.revoked_at
        )
        assert len(events) in ({1} if case == "two_grants" else {2, 3})
    finally:
        await engine.dispose()


async def test_migration_backfills_existing_consent(db):
    # Only the explicitly configured test DB is reachable through this fixture.
    # An independent random schema exercises a non-empty 0002 -> 0003 upgrade.
    original_schema = await db.scalar(text("SELECT current_schema()"))
    schema = "cc_backfill_" + uuid4().hex
    await db.execute(text(f'CREATE SCHEMA "{schema}"'))
    await db.execute(text(f'SET search_path TO "{schema}"'))

    def upgrade(sync_connection, names):
        with Operations.context(MigrationContext.configure(sync_connection)):
            for name in names:
                migration(name).upgrade()

    try:
        await db.bind.run_sync(upgrade, ["0001_foundational_identity", "0002_patient_invitations"])
        patient, profile = await add_patient(db)
        _, doctor = await add_practitioner(db)
        now = datetime.now(UTC)
        consent = Consent(
            patient_id=profile.id,
            practitioner_id=doctor.id,
            status="revoked",
            granted_at=now,
            revoked_at=now,
        )
        db.add(consent)
        await db.commit()
        # Let the session own this transaction before running migration DDL
        # through its bound connection, so cleanup can commit predictably.
        await db.execute(text("SELECT 1"))
        await db.bind.run_sync(upgrade, ["0003_consent_history"])
        events = await history(db, str(consent.id))
        assert len(events) == 1 and events[0].kind == "baseline"
        assert events[0].status == "revoked" and events[0].actor_id is None
        assert events[0].granted_at == now and events[0].revoked_at == now
        assert events[0].consent_id == consent.id and patient.id == profile.account_id
        await db.commit()
    finally:
        await db.rollback()
        await db.execute(text(f'SET search_path TO "{original_schema}"'))
        await db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await db.commit()
