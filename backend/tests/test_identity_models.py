"""Offline schema checks and opt-in PostgreSQL constraint tests.

Set TEST_DATABASE_URL to a postgresql+asyncpg database ending in _test.
Tests create only a random private schema there, then remove that schema.
No application database is migrated or reset.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import MetaData, Table, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import configure_mappers
from sqlalchemy.schema import CreateTable

from app.core.database import Base
from app.models import PatientPractitionerConsent as Consent
from app.models import PatientProfile, PractitionerProfile, User
from tests.database import migration


def test_migration_matches_model_schema(monkeypatch):
    """Frozen migration and ORM produce identical PostgreSQL table definitions."""
    configure_mappers()
    metadata = MetaData()

    class Recorder:
        @staticmethod
        def create_table(name, *items):
            Table(name, metadata, *items)

    revision = migration()
    monkeypatch.setattr(revision, "op", Recorder)
    revision.upgrade()
    assert set(metadata.tables) == set(Base.metadata.tables)
    dialect = postgresql.dialect()
    for name, table in metadata.tables.items():
        # Constraint order does not affect the generated schema.
        actual = str(CreateTable(table).compile(dialect=dialect))
        expected = str(CreateTable(Base.metadata.tables[name]).compile(dialect=dialect))
        assert sorted(line.strip().rstrip(",") for line in actual.splitlines()) == sorted(
            line.strip().rstrip(",") for line in expected.splitlines()
        )


def test_no_implicit_consent_or_authorization_defaults():
    assert Consent.__table__.c.status.default is None
    assert Consent.__table__.c.status.server_default is None
    assert PatientProfile.__table__.c.is_active.default.arg is False
    assert PractitionerProfile.__table__.c.is_authorized.default.arg is False
    assert User.__table__.c.is_active.default.arg is False
    assert PatientProfile.__table__.c.account_id.nullable  # pending onboarding


async def account(db, role):
    user = User(
        identity=f"{uuid4().hex}@synthetic.invalid",
        hashed_password="$test-hash",
        role=role,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


async def practitioner(db):
    user = await account(db, "practitioner")
    profile = PractitionerProfile(account_id=user.id, is_authorized=True)
    db.add(profile)
    await db.flush()
    return profile


async def patient(db):
    user = await account(db, "patient")
    now = datetime.now(UTC)
    profile = PatientProfile(account_id=user.id, is_active=True, verified_at=now, activated_at=now)
    db.add(profile)
    await db.flush()
    return profile


async def test_pending_activation_and_pair_isolation(db):
    p1, p2 = await practitioner(db), await practitioner(db)
    pending = PatientProfile(initiated_by_id=p1.id)
    db.add(pending)
    await db.flush()
    assert not pending.is_active and pending.account_id is None
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(
                Consent(
                    patient_id=pending.id,
                    practitioner_id=p1.id,
                    status="granted",
                    granted_at=datetime.now(UTC),
                )
            )
            await db.flush()
    user = await account(db, "patient")
    pending.account_id = user.id
    pending.verified_at = pending.activated_at = datetime.now(UTC)
    pending.is_active = True
    await db.flush()
    assert await db.scalar(select(func.count()).select_from(Consent)) == 0
    other = await patient(db)
    c1 = Consent(patient=pending, practitioner=p1, status="granted", granted_at=datetime.now(UTC))
    c2 = Consent(patient=pending, practitioner=p2, status="granted", granted_at=datetime.now(UTC))
    db.add_all([c1, c2])
    await db.flush()
    c1.status, c1.revoked_at = "revoked", datetime.now(UTC)
    await db.flush()
    assert c2.status == "granted"
    assert (
        await db.scalar(
            select(func.count()).select_from(Consent).where(Consent.patient_id == other.id)
        )
        == 0
    )
    await db.refresh(pending, ["consents"])
    assert len(pending.consents) == 2


@pytest.mark.parametrize(
    "case",
    [
        "duplicate_identity",
        "invalid_role",
        "empty_hash",
        "unnormalized_identity",
        "wrong_profile_role",
        "duplicate_profile",
        "unverified_activation",
        "missing_origin",
        "duplicate_consent",
        "invalid_status",
        "missing_revocation",
        "early_revocation",
        "missing_patient",
        "inactive_consent",
    ],
)
async def test_database_rejects_invalid_foundation(db, case):
    p = await practitioner(db)
    pt = await patient(db)
    now = datetime.now(UTC)
    existing = Consent(patient_id=pt.id, practitioner_id=p.id, status="granted", granted_at=now)
    db.add(existing)
    await db.flush()
    user = await account(db, "patient")
    cases = {
        "duplicate_identity": User(identity=user.identity, hashed_password="hash", role="patient"),
        "invalid_role": User(
            identity="unique@synthetic.invalid", hashed_password="hash", role="admin"
        ),
        "empty_hash": User(identity="unique@synthetic.invalid", hashed_password="", role="patient"),
        "unnormalized_identity": User(
            identity="Upper@synthetic.invalid", hashed_password="hash", role="patient"
        ),
        "wrong_profile_role": PractitionerProfile(account_id=user.id),
        "duplicate_profile": PatientProfile(account_id=pt.account_id),
        "unverified_activation": PatientProfile(
            account_id=user.id, is_active=True, activated_at=now
        ),
        "missing_origin": PatientProfile(),
        "duplicate_consent": Consent(
            patient_id=pt.id, practitioner_id=p.id, status="granted", granted_at=now
        ),
        "invalid_status": Consent(
            patient_id=pt.id, practitioner_id=p.id, status="pending", granted_at=now
        ),
        "missing_revocation": Consent(
            patient_id=pt.id, practitioner_id=p.id, status="revoked", granted_at=now
        ),
        "early_revocation": Consent(
            patient_id=pt.id,
            practitioner_id=p.id,
            status="revoked",
            granted_at=now,
            revoked_at=datetime(2000, 1, 1, tzinfo=UTC),
        ),
        "missing_patient": Consent(
            patient_id=uuid4(), practitioner_id=p.id, status="granted", granted_at=now
        ),
        "inactive_consent": Consent(
            patient_id=pt.id,
            patient_active=False,
            practitioner_id=p.id,
            status="granted",
            granted_at=now,
        ),
    }
    candidate = cases[case]
    if isinstance(candidate, Consent) and case != "duplicate_consent":
        # Avoid a duplicate-pair failure masking the constraint under test.
        candidate.practitioner_id = (await practitioner(db)).id
    constraint_names = {
        "duplicate_identity": "users_identity_key",
        "invalid_role": "ck_users_role",
        "empty_hash": "ck_users_password_present",
        "unnormalized_identity": "ck_users_identity_normalized",
        "wrong_profile_role": "fk_practitioner_account",
        "duplicate_profile": "patient_profiles_account_id_key",
        "unverified_activation": "ck_patient_activation",
        "missing_origin": "ck_patient_onboarding_origin",
        "duplicate_consent": "uq_consent_pair",
        "invalid_status": "ck_consent_status",
        "missing_revocation": "ck_consent_timestamps",
        "early_revocation": "ck_consent_timestamps",
        "missing_patient": "fk_consent_active_patient",
        "inactive_consent": "ck_consent_active_patient",
    }
    with pytest.raises(IntegrityError) as error:
        async with db.begin_nested():
            db.add(candidate)
            await db.flush()
    assert constraint_names[case] in str(error.value.orig)
