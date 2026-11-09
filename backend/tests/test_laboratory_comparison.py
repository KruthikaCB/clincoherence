"""Explicit laboratory-pair policy; no clinical interpretation or implicit pairing."""

import asyncio
import secrets
from datetime import timedelta, timezone
from decimal import Decimal, Inexact, Rounded, localcontext
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import decode_access_token
from app.models import ClinicalFactVersion, ConfirmationDecision
from app.schemas.laboratory_comparison import VersionPair
from app.services.consistency import version_input
from app.services.consistency_evaluator import ConsistencyInputError
from app.services.laboratory_comparison import compare_patient_laboratories
from app.services.laboratory_evaluator import LaboratoryInputError, evaluate_laboratory_pairs
from tests.test_auth import add_patient, add_practitioner, headers
from tests.test_clinical_facts import entry, path, wait_until_blocked
from tests.test_clinical_facts import race_environment as race_environment
from tests.test_consents import grant, revoke
from tests.test_consistency import AUTHOR, PATIENT, WHEN, correction, fact


@pytest.fixture(autouse=True)
def signing_configuration(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", secrets.token_urlsafe(48))


def lab(number=10, value="7.1", **changes):
    return fact(
        number, fact={"kind": "laboratory", "code": "hba1c", "value": value, "unit": "%"}, **changes
    )


def pair(left, right):
    return VersionPair(first_version_id=left.version_id, second_version_id=right.version_id)


def evaluate(left, right, pairs=None):
    return evaluate_laboratory_pairs(
        PATIENT, [left, right], [left, right], pairs if pairs is not None else [pair(left, right)]
    )


@pytest.mark.parametrize(
    ("earlier", "later", "difference"),
    [
        ("7.1", "8.2", "1.1"),
        ("8.2", "7.1", "-1.1"),
        ("7.10", "7.1000", "0"),
        ("-0.0", "0", "0"),
        ("-8.2", "-7.1", "1.1"),
        ("99999999999999.9999999999", "0.0000000001", "-99999999999999.9999999998"),
        ("-99999999999999.9999999999", "99999999999999.9999999999", "199999999999999.9999999998"),
    ],
)
def test_exact_arithmetic_without_interpretation(earlier, later, difference):
    a = lab(value=earlier)
    b = lab(20, later, observed_at=WHEN + timedelta(days=1))
    result = evaluate(b, a)
    assert not result.consistency.findings and not result.not_comparable
    comparison = result.comparisons[0]
    assert comparison.numeric_difference == Decimal(difference)
    assert str(comparison.numeric_difference) == difference
    assert comparison.earlier_version_id == a.version_id
    assert comparison.later_version_id == b.version_id
    assert comparison.unit == "%"
    assert "outcome" not in comparison.model_dump()


def test_decimal_environment_independence_and_input_purity():
    a = lab(value="99999999999999.9999999999")
    b = lab(20, "0.0000000001", observed_at=WHEN + timedelta(days=1))
    before = [a.model_dump_json(), b.model_dump_json()]
    expected = evaluate(a, b).model_dump_json()
    with localcontext() as ctx:
        ctx.prec = 2
        ctx.traps[Inexact] = ctx.traps[Rounded] = True
        ctx.clear_flags()
        assert evaluate(b, a).model_dump_json() == expected
        assert not any(ctx.flags.values())
    assert before == [a.model_dump_json(), b.model_dump_json()]


def test_pair_deduplication_order_and_numeric_timezone_normalization():
    a = lab()
    b = lab(
        20,
        "8.200",
        observed_at=(WHEN + timedelta(days=1)).astimezone(timezone(timedelta(hours=5, minutes=30))),
    )
    c = lab(30, "9.3", observed_at=WHEN + timedelta(days=2))
    expected = evaluate_laboratory_pairs(PATIENT, [a, b, c], [a, b, c], [pair(a, b), pair(a, c)])
    actual = evaluate_laboratory_pairs(
        PATIENT, [c, b, a], [b, a, c], [pair(c, a), pair(b, a), pair(a, b)]
    )
    assert actual.model_dump_json() == expected.model_dump_json()
    assert len(actual.comparisons) == 2
    assert all(
        comparison.later_version_id != c.version_id or comparison.earlier_version_id == a.version_id
        for comparison in actual.comparisons
    )
    assert "evaluated_at" not in actual.model_dump()


def test_no_automatic_pairing():
    a, b = lab(), lab(20, observed_at=WHEN + timedelta(days=1))
    result = evaluate(a, b, [])
    assert result.requested_pairs == result.comparisons == result.not_comparable == ()
    assert result.checks_run == ()
    assert {s.reason_code for s in result.checks_not_applicable} == {"NO_PAIRS_REQUESTED"}


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        (
            {"fact": {"kind": "medication", "medication_name": "synthetic metformin"}},
            "LABORATORY_FACTS_REQUIRED",
        ),
        (
            {"fact": {"kind": "laboratory", "code": "glucose", "value": "110", "unit": "mg/dL"}},
            "LABORATORY_CODE_MISMATCH",
        ),
        (
            {"fact": {"kind": "laboratory", "code": "hba1c", "value": "50", "unit": "mmol/mol"}},
            "UNIT_MISMATCH_NO_CONVERSION",
        ),
        ({"author_id": UUID(int=99)}, "AUTHOR_MISMATCH"),
        (
            {"attribution": "practitioner_managed", "author_role": "practitioner"},
            "ATTRIBUTION_MISMATCH",
        ),
        ({"observed_at": WHEN}, "DISTINCT_OBSERVATION_TIMES_REQUIRED"),
    ],
)
def test_eligibility_reasons(changes, reason):
    a = lab()
    b = fact(
        20,
        observed_at=WHEN + timedelta(days=1),
        **{k: v for k, v in changes.items() if k != "observed_at"},
    )
    if "observed_at" in changes:
        b = fact(20, **changes)
    result = evaluate(a, b)
    assert not result.comparisons
    assert len(result.not_comparable) == 1
    assert result.not_comparable[0].reason_code == reason
    assert result.not_comparable[0].category == "NOT_COMPARABLE"


def test_self_pair_is_not_distinct_roots():
    a = lab()
    result = evaluate_laboratory_pairs(PATIENT, [a], [a], [pair(a, a)])
    assert not result.comparisons
    assert result.not_comparable[0].reason_code == "DISTINCT_FACT_ROOTS_REQUIRED"


@pytest.mark.parametrize("case", ["missing", "superseded", "pending", "rejected"])
def test_unavailable_versions_fail_closed_without_identifiers(case):
    a, b = lab(), lab(20, observed_at=WHEN + timedelta(days=1))
    history, current = [a, b], [a, b]
    wanted = VersionPair(first_version_id=a.version_id, second_version_id=uuid4())
    if case == "superseded":
        c = correction(a, 11)
        current, history, wanted = [c, b], [a, c, b], pair(a, b)
    elif case in {"pending", "rejected"}:
        a = lab(status=case.upper())
        current, history, wanted = [a, b], [a, b], pair(a, b)
    with pytest.raises(LaboratoryInputError) as error:
        evaluate_laboratory_pairs(PATIENT, current, history, [wanted])
    assert str(error.value) == "Requested versions unavailable"


def test_integrity_failure_blocks_arithmetic_and_corrected_current_is_used():
    a, b = lab(), lab(20, observed_at=WHEN + timedelta(days=1))
    c = correction(a, 11, version=4)
    valid = evaluate_laboratory_pairs(PATIENT, [c, b], [a, c, b], [pair(c, b)])
    assert len(valid.comparisons) == 1
    assert valid.comparisons[0].earlier_version_id == c.version_id
    broken = correction(a, 11, correction_reason=" ")
    invalid = evaluate_laboratory_pairs(PATIENT, [broken, b], [a, broken, b], [pair(broken, b)])
    assert any(f.category == "INTEGRITY_ERROR" for f in invalid.consistency.findings)
    assert not invalid.comparisons and not invalid.not_comparable
    assert invalid.checks_not_applicable[0].reason_code == "VALID_ACCEPTED_STATE_REQUIRED"


@pytest.mark.parametrize("location", ["current", "history"])
def test_mixed_patient_input_has_no_result(location):
    a, b = lab(), lab(20, patient_id=uuid4())
    with pytest.raises(ConsistencyInputError) as error:
        evaluate_laboratory_pairs(PATIENT, [a, b] if location == "current" else [a], [a, b], [])
    assert str(error.value) == "Patient scope mismatch"


async def test_service_read_only_and_final_authorization(monkeypatch):
    from app.services import laboratory_comparison as service

    a, b = lab(), lab(20, observed_at=WHEN + timedelta(days=1))
    loader = AsyncMock(return_value=([a, b], [a, b]))
    authorize = AsyncMock()
    monkeypatch.setattr(service, "load_accepted_state", loader)
    monkeypatch.setattr(service, "authorize_patient_scope", authorize)
    async with AsyncSession() as db:
        db.commit, db.flush = AsyncMock(), AsyncMock()
        first = await compare_patient_laboratories(db, AUTHOR, PATIENT, [pair(a, b)])
        second = await compare_patient_laboratories(db, AUTHOR, PATIENT, [pair(b, a)])
        assert first.evaluation == second.evaluation
        assert first.evaluated_at.tzinfo is not None
        assert authorize.await_count == loader.await_count == 2
        db.commit.assert_not_awaited()
        db.flush.assert_not_awaited()
        authorize.side_effect = HTTPException(403, "Patient scope denied")
        with pytest.raises(HTTPException) as error:
            await compare_patient_laboratories(db, AUTHOR, PATIENT, [pair(a, b)])
        assert error.value.status_code == 403


async def test_service_unavailable_pair_error_is_sanitized(monkeypatch):
    from app.services import laboratory_comparison as service

    a = lab()
    monkeypatch.setattr(service, "load_accepted_state", AsyncMock(return_value=([a], [a])))
    hidden = uuid4()
    async with AsyncSession() as db:
        with pytest.raises(HTTPException) as error:
            await compare_patient_laboratories(
                db,
                AUTHOR,
                PATIENT,
                [VersionPair(first_version_id=a.version_id, second_version_id=hidden)],
            )
        assert error.value.status_code == 409
        assert error.value.detail == "Laboratory comparison unavailable"
        assert str(hidden) not in str(error.value.detail)


async def test_database_snapshot_no_mutation_and_corrected_pair(db, api):
    patient, profile = await add_patient(db)
    patient_id, account_id = profile.id, patient.id
    a = (await api.post(path(patient_id), headers=headers(patient), json=entry())).json()
    later = {**entry("8.2"), "observed_at": "2026-01-02T10:00:00Z"}
    b = (await api.post(path(patient_id), headers=headers(patient), json=later)).json()
    c = (
        await api.post(
            f"{path(patient_id)}/{a['fact_id']}/corrections",
            headers=headers(patient),
            json={
                **entry("7.3"),
                "expected_version_id": a["version_id"],
                "correction_reason": "synthetic correction",
            },
        )
    ).json()
    requested = [
        VersionPair(first_version_id=UUID(c["version_id"]), second_version_id=UUID(b["version_id"]))
    ]
    before = {
        row.id: version_input(row).model_dump_json()
        for row in await db.scalars(select(ClinicalFactVersion))
    }
    decisions = list(await db.scalars(select(ConfirmationDecision.proposal_id)))
    result = await compare_patient_laboratories(db, account_id, patient_id, requested)
    assert result.evaluation.comparisons[0].numeric_difference == Decimal("0.9")
    assert len(result.evaluation.consistency.snapshot.history_version_ids) == 3
    assert db.in_transaction() and not db.new and not db.dirty and not db.deleted
    assert before == {
        row.id: version_input(row).model_dump_json()
        for row in await db.scalars(select(ClinicalFactVersion))
    }
    assert decisions == list(await db.scalars(select(ConfirmationDecision.proposal_id)))
    await db.rollback()


async def test_database_missing_foreign_and_nonaccepted_pair_ids(db, api):
    from tests.test_clinical_facts import candidate

    patient, profile = await add_patient(db)
    other, other_profile = await add_patient(db)
    account_id, patient_id = patient.id, profile.id
    local = (await api.post(path(patient_id), headers=headers(patient), json=entry())).json()
    foreign = (await api.post(path(other_profile.id), headers=headers(other), json=entry())).json()
    pending = candidate(patient_id, account_id)
    rejected = candidate(patient_id, account_id, status="REJECTED")
    db.add_all([pending, rejected])
    await db.commit()
    ids = [uuid4(), UUID(foreign["version_id"]), pending.id, rejected.id]
    for unavailable in ids:
        with pytest.raises(HTTPException) as error:
            await compare_patient_laboratories(
                db,
                account_id,
                patient_id,
                [
                    VersionPair(
                        first_version_id=UUID(local["version_id"]), second_version_id=unavailable
                    )
                ],
            )
        assert error.value.status_code == 409
        assert error.value.detail == "Laboratory comparison unavailable"
    await db.rollback()


async def test_database_current_consent_and_patient_scope(db, api):
    patient, profile = await add_patient(db)
    doctor, practitioner = await add_practitioner(db)
    account_id, patient_id, practitioner_id = doctor.id, profile.id, practitioner.id
    await db.commit()
    with pytest.raises(HTTPException) as error:
        await compare_patient_laboratories(db, account_id, patient_id, [])
    assert error.value.status_code == 403
    await db.rollback()
    await db.refresh(patient)
    assert (await grant(api, patient, practitioner_id)).status_code == 200
    assert not (
        await compare_patient_laboratories(db, account_id, patient_id, [])
    ).evaluation.comparisons
    await db.rollback()
    await db.refresh(patient)
    assert (await revoke(api, patient, practitioner_id)).status_code == 200
    with pytest.raises(HTTPException) as error:
        await compare_patient_laboratories(db, account_id, patient_id, [])
    assert error.value.status_code == 403
    await db.rollback()


async def test_database_other_patient_cannot_select_scope(db):
    patient, _ = await add_patient(db)
    _, other = await add_patient(db)
    with pytest.raises(HTTPException) as error:
        await compare_patient_laboratories(db, patient.id, other.id, [])
    assert error.value.status_code == 403
    await db.rollback()


@pytest.mark.parametrize("revoke_first", [True, False])
async def test_database_revocation_and_read_gate(db, race_environment, revoke_first):
    env = race_environment
    env["hold_revoke"] = revoke_first
    account_id = decode_access_token(env["doctor_auth"]["Authorization"].split()[1])
    async with (
        AsyncClient(transport=ASGITransport(app=env["app"]), base_url="http://test") as client,
        AsyncSession(env["engine"], expire_on_commit=False) as session,
    ):

        async def check():
            env["pids"]["clinical"] = await session.scalar(text("SELECT pg_backend_pid()"))
            return await compare_patient_laboratories(session, account_id, env["patient_id"], [])

        async def revoke_access():
            return await client.post(
                f"/api/v1/patients/me/consents/{env['doctor_profile_id']}/revoke",
                headers=env["auth"],
            )

        if revoke_first:
            revoking = asyncio.create_task(revoke_access())
            await asyncio.wait_for(env["commit_ready"].wait(), 3)
            checking = asyncio.create_task(check())
            try:
                await wait_until_blocked(db, env, "clinical")
            finally:
                env["commit_release"].set()
            assert (await revoking).status_code == 200
            with pytest.raises(HTTPException) as error:
                await checking
            assert error.value.status_code == 403
            await session.rollback()
        else:
            await check()
            revoking = asyncio.create_task(revoke_access())
            try:
                await wait_until_blocked(db, env, "revoke")
                assert not revoking.done()
            finally:
                await session.rollback()
            assert (await revoking).status_code == 200


def test_mixed_eligible_and_ineligible_requests_are_separate_and_no_partial_on_missing():
    a = lab()
    b = lab(20, observed_at=WHEN + timedelta(days=1))
    c = fact(30, fact={"kind": "laboratory", "code": "glucose", "value": "110", "unit": "mg/dL"})
    current = [a, b, c]
    result = evaluate_laboratory_pairs(PATIENT, current, current, [pair(a, b), pair(a, c)])
    assert len(result.comparisons) == len(result.not_comparable) == 1
    assert result.not_comparable[0].reason_code == "LABORATORY_CODE_MISMATCH"
    with pytest.raises(LaboratoryInputError):
        evaluate_laboratory_pairs(
            PATIENT,
            current,
            current,
            [pair(a, b), VersionPair(first_version_id=a.version_id, second_version_id=uuid4())],
        )
