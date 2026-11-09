"""Deterministic structural rules and patient-locked read-only integration."""

import asyncio
import secrets
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import ClinicalFactVersion, ConfirmationDecision
from app.schemas.consistency import FactVersion
from app.services.consistency import check_patient_consistency
from app.services.consistency_evaluator import ConsistencyInputError, evaluate_consistency
from tests.test_auth import add_patient, add_practitioner, headers
from tests.test_clinical_facts import candidate, entry, path, wait_until_blocked
from tests.test_clinical_facts import race_environment as race_environment
from tests.test_consents import grant, revoke

PATIENT = UUID(int=1)
AUTHOR = UUID(int=2)
WHEN = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def signing_configuration(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", secrets.token_urlsafe(48))


def fact(number=10, **changes):
    data = dict(
        version_id=UUID(int=number),
        fact_id=UUID(int=number),
        patient_id=PATIENT,
        version=1,
        attribution="patient_reported",
        author_id=AUTHOR,
        author_role="patient",
        fact={"kind": "laboratory", "code": "hba1c", "value": "7.1", "unit": "%"},
        provenance={
            "source": "synthetic fixture",
            "reference": "fixture-1",
            "data_origin": "synthetic",
        },
        observed_at=WHEN,
        entered_at=WHEN + timedelta(days=2),
    )
    data.update(changes)
    return FactVersion.model_validate(data)


def correction(parent, number=11, **changes):
    data = parent.model_dump()
    data.update(
        version_id=UUID(int=number),
        version=parent.version + 1,
        previous_version_id=parent.version_id,
        previous_version=parent.version,
        correction_reason="synthetic transcription correction",
        entered_at=parent.entered_at + timedelta(days=1),
    )
    data.update(changes)
    return FactVersion.model_validate(data)


def reasons(result):
    return {f.reason_code for f in result.findings}


def test_empty_state_and_no_blanket_not_comparable():
    result = evaluate_consistency(PATIENT, [], [])
    assert not result.findings
    assert result.checks_not_applicable[0].reason_code == "NO_MATCHING_OBSERVATION_PAIRS"


def test_different_times_retrospective_observations_and_no_thresholds():
    a = fact()
    b = fact(
        20,
        observed_at=WHEN - timedelta(days=90),
        entered_at=WHEN + timedelta(days=10),
        fact={"kind": "laboratory", "code": "hba1c", "value": "999", "unit": "%"},
    )
    result = evaluate_consistency(PATIENT, [a, b], [b, a])
    assert result.findings == ()


def test_correction_history_skipped_numbers_and_changed_observation_time():
    a = fact()
    b = correction(a, version=4, observed_at=WHEN - timedelta(days=20))
    result = evaluate_consistency(PATIENT, [b], [b, a])
    assert not result.findings
    assert result.snapshot.current_version_ids == (b.version_id,)
    assert result.snapshot.history_version_ids == (a.version_id, b.version_id)


@pytest.mark.parametrize("current_case", ["old", "both", "none", "missing", "changed"])
def test_current_selection(current_case):
    a = fact()
    b = correction(a)
    current = {
        "old": [a],
        "both": [a, b],
        "none": [],
        "missing": [fact(30)],
        "changed": [correction(a, correction_reason="different stored content")],
    }[current_case]
    result = evaluate_consistency(PATIENT, current, [a, b])
    assert any(f.rule_id == "current_selection" for f in result.findings)
    assert "comparison" not in result.checks_run


@pytest.mark.parametrize("status", ["PENDING", "REJECTED"])
@pytest.mark.parametrize("location", ["current", "history"])
def test_nonaccepted_input_short_circuits_content_checks(status, location):
    a = fact(status=status)
    result = evaluate_consistency(
        PATIENT, [a] if location == "current" else [], [a] if location == "history" else []
    )
    assert reasons(result) == {"NON_ACCEPTED_INPUT"}
    assert result.checks_run == ("membership",)


@pytest.mark.parametrize("location", ["current", "history"])
def test_mixed_patient_fails_before_any_result_or_identifiers(location):
    foreign_id = uuid4()
    foreign = fact(20, patient_id=foreign_id)
    with pytest.raises(ConsistencyInputError) as error:
        evaluate_consistency(
            PATIENT,
            [foreign] if location == "current" else [],
            [foreign] if location == "history" else [],
        )
    assert str(error.value) == "Patient scope mismatch"
    assert str(foreign_id) not in str(error.value)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"previous_version_id": UUID(int=999)}, "ACCEPTED_PARENT_MISSING"),
        ({"previous_version": 8}, "PARENT_VERSION_MISMATCH"),
        ({"correction_reason": "  "}, "CORRECTION_REASON_REQUIRED"),
        ({"entered_at": WHEN}, "CORRECTION_ENTRY_BEFORE_PARENT"),
        ({"attribution": "practitioner_managed"}, "AUTHOR_CATEGORY_MISMATCH"),
    ],
)
def test_chain_and_provenance_errors(change, reason):
    a = fact()
    b = correction(a, **change)
    result = evaluate_consistency(PATIENT, [b], [a, b])
    assert reason in reasons(result)
    assert UUID(int=999) not in {i for finding in result.findings for i in finding.fact_version_ids}


def test_root_parent_identity_branch_and_duplicate_history():
    a, other = fact(), fact(20)
    b = correction(a, previous_version_id=other.version_id)
    assert "PARENT_IDENTITY_MISMATCH" in reasons(
        evaluate_consistency(PATIENT, [b, other], [a, other, b])
    )
    b = correction(a)
    c = correction(a, 12, version=4)
    assert "PARENT_NOT_PREVIOUS_ACCEPTED_VERSION" in reasons(
        evaluate_consistency(PATIENT, [c], [a, b, c])
    )
    assert "ROOT_MISSING_OR_INVALID" in reasons(evaluate_consistency(PATIENT, [b], [b]))
    assert "DUPLICATE_VERSION_ID" in reasons(evaluate_consistency(PATIENT, [a], [a, a]))
    broken = fact(version=0)
    assert "INVALID_VERSION_NUMBER" in reasons(evaluate_consistency(PATIENT, [broken], [broken]))


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"confirmed_by_id": AUTHOR}, "STRUCTURED_CONFIRMATION_MARKER"),
        ({"entry_method": "LLM_EXTRACTED"}, "EXTRACTED_CONFIRMATION_MISSING_OR_WRONG_SPEAKER"),
        (
            {
                "entry_method": "LLM_EXTRACTED",
                "confirmed_by_id": UUID(int=99),
                "confirmed_at": WHEN,
            },
            "EXTRACTED_CONFIRMATION_MISSING_OR_WRONG_SPEAKER",
        ),
        (
            {
                "entry_method": "LLM_EXTRACTED",
                "confirmed_by_id": AUTHOR,
                "confirmed_at": WHEN + timedelta(days=3),
            },
            "CONFIRMATION_AFTER_ACCEPTED_ENTRY",
        ),
    ],
)
def test_acceptance_provenance_and_chronology(change, reason):
    a = fact(**change)
    assert reason in reasons(evaluate_consistency(PATIENT, [a], [a]))


def test_defensible_duplicate_decimal_timezone_normalization_and_purity():
    a = fact()
    b = fact(
        20,
        observed_at=WHEN.astimezone(timezone(timedelta(hours=5, minutes=30))),
        fact={"kind": "laboratory", "code": "hba1c", "value": "7.1000", "unit": "%"},
    )
    before = [a.model_dump_json(), b.model_dump_json()]
    result = evaluate_consistency(PATIENT, [a, b], [a, b])
    assert [f.category for f in result.findings] == ["POSSIBLE_DUPLICATE"]
    assert (
        evaluate_consistency(PATIENT, [b, a], [b, a]).model_dump_json() == result.model_dump_json()
    )
    assert before == [a.model_dump_json(), b.model_dump_json()]
    assert "evaluated_at" not in type(result).model_fields


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            {"fact": {"kind": "laboratory", "code": "hba1c", "value": "50", "unit": "mmol/mol"}},
            "DIFFERENT_UNITS_NO_CONVERSION",
        ),
        (
            {"fact": {"kind": "laboratory", "code": "hba1c", "value": "8", "unit": "%"}},
            "EVENT_IDENTITY_NOT_ESTABLISHED",
        ),
        ({"author_id": UUID(int=25)}, "EVENT_IDENTITY_NOT_ESTABLISHED"),
        (
            {"provenance": {"source": "synthetic fixture", "data_origin": "synthetic"}},
            "EVENT_IDENTITY_NOT_ESTABLISHED",
        ),
    ],
)
def test_attempted_comparisons_do_not_invent_contradictions(change, reason):
    a, b = fact(), fact(20, **change)
    result = evaluate_consistency(PATIENT, [a, b], [a, b])
    assert reasons(result) == {reason}
    assert result.findings[0].category == "NOT_COMPARABLE"


async def test_service_scopes_state_excludes_pending_and_does_not_mutate(db, api):
    patient, profile = await add_patient(db)
    other, other_profile = await add_patient(db)
    first = (await api.post(path(profile.id), headers=headers(patient), json=entry())).json()
    await api.post(path(other_profile.id), headers=headers(other), json=entry("8"))
    original = await db.get(ClinicalFactVersion, UUID(first["version_id"]))
    db.add_all(
        [
            candidate(profile.id, patient.id),
            candidate(profile.id, patient.id, status="REJECTED"),
            candidate(profile.id, patient.id, previous=original),
        ]
    )
    await db.commit()
    # Use an actual accepted correction after a pending version: version 3 links to 1.
    corrected = await api.post(
        f"{path(profile.id)}/{first['fact_id']}/corrections",
        headers=headers(patient),
        json={
            **entry("7.3"),
            "expected_version_id": first["version_id"],
            "correction_reason": "synthetic correction",
        },
    )
    assert corrected.status_code == 201
    before = [
        (r.id, dict(r.payload), r.status) for r in await db.scalars(select(ClinicalFactVersion))
    ]
    decisions = list(await db.scalars(select(ConfirmationDecision.proposal_id)))
    run = await check_patient_consistency(db, patient.id, profile.id)
    again = await check_patient_consistency(db, patient.id, profile.id)
    assert run.evaluation == again.evaluation and not run.evaluation.findings
    assert run.evaluation.snapshot.current_version_ids == (UUID(corrected.json()["version_id"]),)
    assert len(run.evaluation.snapshot.history_version_ids) == 2
    assert [
        (r.id, dict(r.payload), r.status) for r in await db.scalars(select(ClinicalFactVersion))
    ] == before
    assert list(await db.scalars(select(ConfirmationDecision.proposal_id))) == decisions
    assert not db.new and not db.dirty and not db.deleted
    assert db.in_transaction()  # caller still owns release/consent gate
    await db.rollback()


async def test_service_unauthorized_practitioner_then_grant_and_revocation(db, api):
    patient, profile = await add_patient(db)
    doctor, practitioner = await add_practitioner(db)
    doctor_id, patient_id, practitioner_id = doctor.id, profile.id, practitioner.id
    # Helpers flush only: persist setup before rolling back read transactions.
    await db.commit()
    with pytest.raises(HTTPException) as error:
        await check_patient_consistency(db, doctor_id, patient_id)
    assert error.value.status_code == 403
    await db.rollback()
    assert patient in db  # Read cleanup must not expunge committed setup.
    await db.refresh(patient)
    assert (await grant(api, patient, practitioner_id)).status_code == 200
    assert not (await check_patient_consistency(db, doctor_id, patient_id)).evaluation.findings
    await db.rollback()
    await db.refresh(patient)
    assert (await revoke(api, patient, practitioner_id)).status_code == 200
    with pytest.raises(HTTPException) as error:
        await check_patient_consistency(db, doctor_id, patient_id)
    assert error.value.status_code == 403
    await db.rollback()


async def test_service_patient_cannot_read_another_patient(db):
    patient, _ = await add_patient(db)
    _, other_profile = await add_patient(db)
    with pytest.raises(HTTPException) as error:
        await check_patient_consistency(db, patient.id, other_profile.id)
    assert error.value.status_code == 403
    assert str(other_profile.id) not in str(error.value.detail)
    await db.rollback()


async def test_service_read_only_snapshot_and_separate_clock_without_database(monkeypatch):
    from app.services import consistency

    a = fact()
    values = a.model_dump()
    values["id"] = values.pop("version_id")
    values["payload"] = values.pop("fact").copy()
    values["payload"]["value"] = str(values["payload"]["value"])
    row = ClinicalFactVersion(**values)
    lock, authorize = AsyncMock(), AsyncMock()
    monkeypatch.setattr(consistency, "lock_patient_scope", lock)
    monkeypatch.setattr(consistency, "authorize_patient_scope", authorize)
    async with AsyncSession() as db:
        db.scalars = AsyncMock(side_effect=[[row], [row], [row], [row]])
        db.commit = AsyncMock()
        db.flush = AsyncMock()
        first = await check_patient_consistency(db, AUTHOR, PATIENT)
        second = await check_patient_consistency(db, AUTHOR, PATIENT)
        assert first.evaluation == second.evaluation
        assert first.evaluated_at.tzinfo is not None
        assert "evaluated_at" not in first.evaluation.model_dump()
        assert lock.await_count == authorize.await_count == 2
        db.commit.assert_not_awaited()
        db.flush.assert_not_awaited()
        assert not db.new and not db.dirty and not db.deleted


async def test_service_denial_does_not_query_or_echo_values(monkeypatch):
    from app.services import consistency

    monkeypatch.setattr(
        consistency,
        "lock_patient_scope",
        AsyncMock(side_effect=HTTPException(403, "Patient scope denied")),
    )
    async with AsyncSession() as db:
        db.scalars = AsyncMock()
        with pytest.raises(HTTPException) as error:
            await check_patient_consistency(db, AUTHOR, PATIENT)
        assert error.value.status_code == 403
        db.scalars.assert_not_awaited()


@pytest.mark.parametrize("case", ["foreign", "malformed"])
async def test_service_bad_snapshot_is_sanitized(monkeypatch, case):
    from app.services import consistency

    a = fact(patient_id=UUID(int=900))
    values = a.model_dump()
    values["id"] = values.pop("version_id")
    values["payload"] = values.pop("fact")
    if case == "malformed":
        values["patient_id"] = PATIENT
        values["payload"] = {"kind": "secret invalid patient text"}
    row = ClinicalFactVersion(**values)
    monkeypatch.setattr(consistency, "lock_patient_scope", AsyncMock())
    async with AsyncSession() as db:
        db.scalars = AsyncMock(side_effect=[[row], [row]])
        with pytest.raises(HTTPException) as error:
            await check_patient_consistency(db, AUTHOR, PATIENT)
        assert error.value.status_code == 409
        assert error.value.detail == "Accepted state unavailable for consistency evaluation"


async def test_service_refuses_dirty_session_without_flushing():
    async with AsyncSession() as db:
        db.add(ClinicalFactVersion(id=uuid4()))
        with pytest.raises(HTTPException) as error:
            await check_patient_consistency(db, AUTHOR, PATIENT)
        assert error.value.status_code == 409
        assert len(db.new) == 1
        await db.rollback()


async def test_consistency_waiting_on_committed_revocation_is_denied(db, race_environment):
    env = race_environment
    env["hold_revoke"] = True
    from httpx import ASGITransport, AsyncClient

    from app.core.security import decode_access_token

    account_id = decode_access_token(env["doctor_auth"]["Authorization"].split()[1])
    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        revoking = asyncio.create_task(
            client.post(
                f"/api/v1/patients/me/consents/{env['doctor_profile_id']}/revoke",
                headers=env["auth"],
            )
        )
        await asyncio.wait_for(env["commit_ready"].wait(), 3)
        async with AsyncSession(env["engine"], expire_on_commit=False) as session:
            env["pids"]["clinical"] = await session.scalar(text("SELECT pg_backend_pid()"))
            checking = asyncio.create_task(
                check_patient_consistency(session, account_id, env["patient_id"])
            )
            try:
                await wait_until_blocked(db, env, "clinical")
            finally:
                env["commit_release"].set()
            assert (await revoking).status_code == 200
            with pytest.raises(HTTPException) as error:
                await checking
            assert error.value.status_code == 403
            await session.rollback()


async def test_consistency_retains_gate_until_caller_finishes(db, race_environment):
    env = race_environment
    from httpx import ASGITransport, AsyncClient

    from app.core.security import decode_access_token

    account_id = decode_access_token(env["doctor_auth"]["Authorization"].split()[1])
    async with AsyncSession(env["engine"], expire_on_commit=False) as session:
        await check_patient_consistency(session, account_id, env["patient_id"])
        async with AsyncClient(
            transport=ASGITransport(app=env["app"]), base_url="http://test"
        ) as client:
            revoking = asyncio.create_task(
                client.post(
                    f"/api/v1/patients/me/consents/{env['doctor_profile_id']}/revoke",
                    headers=env["auth"],
                )
            )
            try:
                await wait_until_blocked(db, env, "revoke")
                assert not revoking.done()
            finally:
                await session.rollback()
            assert (await revoking).status_code == 200


async def test_confirmed_extraction_is_read_without_changing_speaker_decision(db, api):
    from tests.test_extraction import decide, extract

    patient, profile = await add_patient(db)
    account_id, patient_id = patient.id, profile.id
    auth = headers(patient)
    items = (await extract(api, patient_id, auth)).json()["confirmations"]
    response = await decide(api, patient_id, auth, items[0]["confirmation_id"])
    assert response.status_code == 200
    decision = await db.get(ConfirmationDecision, UUID(items[0]["confirmation_id"]))
    before = (decision.status, decision.decided_by_id, decision.accepted_version_id)
    result = await check_patient_consistency(db, account_id, patient_id)
    assert not result.evaluation.findings
    assert result.evaluation.snapshot.current_version_ids == (decision.accepted_version_id,)
    assert before == (decision.status, decision.decided_by_id, decision.accepted_version_id)
    assert decision.decided_by_id == account_id
    assert len(result.evaluation.snapshot.history_version_ids) == 1
    await db.rollback()
