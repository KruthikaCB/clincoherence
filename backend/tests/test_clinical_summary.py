"""Synthetic recorded-state summaries; no clinical advice or database fallback."""

import secrets
from datetime import timedelta, timezone
from decimal import Decimal, Inexact, Rounded, localcontext
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import ClinicalFactVersion, ConfirmationDecision
from app.services import clinical_summary as service
from app.services.clinical_summary import SummaryInputError, build_clinical_summary
from app.services.consistency import version_input
from app.services.consistency_evaluator import ConsistencyInputError
from tests.test_auth import add_patient, add_practitioner, headers
from tests.test_clinical_facts import entry, path
from tests.test_consents import grant, revoke
from tests.test_consistency import AUTHOR, PATIENT, WHEN, correction, fact


@pytest.fixture(autouse=True)
def signing_configuration(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", secrets.token_urlsafe(48))


def lab(number=10, value="7.1", day=0, code="hba1c", unit="%", **changes):
    return fact(
        number,
        fact={"kind": "laboratory", "code": code, "value": value, "unit": unit},
        observed_at=WHEN + timedelta(days=day),
        entered_at=WHEN + timedelta(days=100),
        **changes,
    )


def medication(number=30):
    return fact(
        number,
        fact={
            "kind": "medication",
            "medication_name": "synthetic metformin",
            "dose_value": "500",
            "dose_unit": "mg",
            "frequency": "recorded twice daily",
        },
    )


def summary(*rows):
    return build_clinical_summary(PATIENT, rows, rows)


def test_multiple_records_observation_order_provenance_and_verified_medications():
    oldest, previous, latest = lab(10, "9.1", -2), lab(11, "8.2", -1), lab(12, "7.1")
    old_renal = lab(20, "60", -2, "egfr", "mL/min/1.73m2")
    renal = lab(21, "58", -1, "egfr", "mL/min/1.73m2")
    med = medication()
    result = summary(previous, old_renal, latest, med, oldest, renal)
    assert [p.value for p in result.hba1c_observations] == list(map(Decimal, ["7.1", "8.2", "9.1"]))
    assert result.latest_hba1c.observed_at == latest.observed_at
    assert result.previous_hba1c.observed_at == previous.observed_at
    assert result.hba1c_change.direction == "DECREASED"
    assert result.hba1c_change.comparison.numeric_difference == Decimal("-1.1")
    assert result.latest_egfr.value == Decimal("58")
    assert result.latest_egfr.unit == "mL/min/1.73m2"
    assert result.latest_egfr.observed_at == renal.observed_at
    assert not result.missing_information
    recorded = result.medications[0]
    assert recorded.source == med
    assert recorded.source.provenance.reference == "fixture-1"
    assert recorded.verification.outcome == "GROUNDED" and recorded.verification.text_verified
    assert "500 mg" in result.text and "current use" in result.text
    assert "2026-01-01T00:00:00Z" in result.text


@pytest.mark.parametrize(
    "earlier,later,direction,delta",
    [
        ("7.1", "8.2", "INCREASED", "1.1"),
        ("8.2", "7.1", "DECREASED", "-1.1"),
        ("7.10", "7.1000", "UNCHANGED", "0"),
    ],
)
def test_direction_is_arithmetic_only(earlier, later, direction, delta):
    result = summary(lab(10, earlier, -1), lab(11, later))
    assert result.hba1c_change.direction == direction
    assert result.hba1c_change.comparison.numeric_difference == Decimal(delta)


def test_missing_information_is_explicit_without_invented_values():
    result = summary()
    assert result.latest_hba1c is result.previous_hba1c is result.latest_egfr is None
    assert result.medications == ()
    assert result.missing_information == ("hba1c", "previous_hba1c", "egfr", "medications")
    assert result.hba1c_change.direction == "UNAVAILABLE"
    assert "No accepted HbA1c" in result.text and "No accepted eGFR" in result.text
    one = summary(lab())
    assert one.latest_hba1c.value == Decimal("7.1") and one.previous_hba1c is None


def test_duplicate_values_collapse_only_at_same_time_and_keep_all_provenance():
    earlier, first = lab(9, "7.10", -1), lab(10, "7.10")
    duplicate = lab(11, "7.1000").model_copy(
        update={"observed_at": WHEN.astimezone(timezone(timedelta(hours=5, minutes=30)))}
    )
    result = summary(duplicate, earlier, first)
    assert len(result.hba1c_observations) == 2
    assert len(result.latest_hba1c.records) == 2
    assert result.previous_hba1c.records[0].source.version_id == earlier.version_id
    assert result.hba1c_change.direction == "UNCHANGED"


def test_conflicting_latest_values_do_not_select_older_unambiguous_value():
    result = summary(lab(10, "7.1"), lab(11, "8.2"), lab(12, "6.9", -1))
    assert result.latest_hba1c.status == "AMBIGUOUS"
    assert result.latest_hba1c.value is result.latest_hba1c.unit is None
    assert len(result.latest_hba1c.records) == 2
    assert result.previous_hba1c.value == Decimal("6.9")
    assert result.hba1c_change.direction == "NOT_COMPARABLE"
    assert result.hba1c_change.comparison is None
    assert "no single value selected" in result.text


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"author_id": UUID(int=99)}, "AUTHOR_MISMATCH"),
        (
            {
                "author_id": UUID(int=99),
                "author_role": "practitioner",
                "attribution": "practitioner_managed",
            },
            "AUTHOR_MISMATCH",
        ),
    ],
)
def test_existing_comparison_author_boundaries_are_preserved(changes, reason):
    result = summary(lab(10, "8.2", -1), lab(11, **changes))
    assert result.hba1c_change.direction == "NOT_COMPARABLE"
    assert result.hba1c_change.reason_code == reason


def test_duplicate_author_paths_cannot_be_discarded_to_force_comparability():
    result = summary(lab(9, "8.2", -1), lab(10), lab(11, author_id=UUID(int=99)))
    assert result.latest_hba1c.status == "RECORDED"
    assert result.hba1c_change.direction == "NOT_COMPARABLE"


def test_incompatible_units_are_not_converted():
    result = summary(lab(10, "8.2", -1), lab(11, "52", unit="mmol/mol"))
    assert result.hba1c_change.reason_code == "UNIT_MISMATCH_NO_CONVERSION"
    assert result.hba1c_change.comparison is None
    tied = summary(lab(10), lab(11, "52", unit="mmol/mol"))
    assert tied.latest_hba1c.status == "AMBIGUOUS"


def test_conflicting_latest_egfr_is_not_replaced_by_an_older_measurement():
    result = summary(
        lab(10, "58", code="egfr", unit="mL/min/1.73m2"),
        lab(11, "60", code="egfr", unit="mL/min/1.73m2"),
        lab(12, "70", -1, "egfr", "mL/min/1.73m2"),
    )
    assert result.latest_egfr.status == "AMBIGUOUS"
    assert result.latest_egfr.value is None
    assert len(result.latest_egfr.records) == 2


def test_correction_leaves_only_and_retrospective_observations():
    root, earlier = lab(10), lab(20, "8.2", -2)
    corrected = correction(
        root,
        11,
        observed_at=WHEN - timedelta(days=1),
        entered_at=WHEN + timedelta(days=101),
        fact={"kind": "laboratory", "code": "hba1c", "value": "7.2", "unit": "%"},
    )
    result = build_clinical_summary(PATIENT, [corrected, earlier], [root, corrected, earlier])
    assert result.latest_hba1c.records[0].source.version_id == corrected.version_id
    assert result.latest_hba1c.value == Decimal("7.2")
    assert root.version_id in result.snapshot.history_version_ids
    assert root.version_id not in result.snapshot.current_version_ids
    with pytest.raises(SummaryInputError):
        build_clinical_summary(PATIENT, [root, earlier], [root, corrected, earlier])


@pytest.mark.parametrize("status", ["PENDING", "REJECTED"])
def test_nonaccepted_input_fails_closed(status):
    with pytest.raises(SummaryInputError, match="Accepted summary context unavailable"):
        summary(lab(status=status))
    with pytest.raises(SummaryInputError):
        build_clinical_summary(PATIENT, [lab()], [lab(), lab(11, status=status)])


def test_foreign_patient_and_missing_speaker_confirmation_fail_closed():
    foreign = lab(patient_id=UUID(int=999))
    with pytest.raises(ConsistencyInputError) as error:
        summary(foreign)
    assert str(foreign.patient_id) not in str(error.value)
    with pytest.raises(SummaryInputError):
        summary(lab(entry_method="LLM_EXTRACTED"))
    confirmed = lab(entry_method="LLM_EXTRACTED", confirmed_by_id=AUTHOR, confirmed_at=WHEN)
    assert summary(confirmed).latest_hba1c.records[0].source.confirmed_by_id == AUTHOR


def test_determinism_decimal_environment_and_no_input_mutations():
    a, b = lab(10, "8.2000000000", -1), lab(11, "7.1000000000")
    before = [r.model_dump_json() for r in (a, b)]
    expected = summary(a, b).model_dump_json()
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = context.traps[Rounded] = True
        assert summary(b, a).model_dump_json() == expected
    assert [r.model_dump_json() for r in (a, b)] == before


def test_more_than_one_verifier_batch_does_not_truncate_records():
    rows = [lab(100 + i, day=-i) for i in range(33)]
    result = summary(*rows)
    assert len(result.hba1c_observations) == 33
    assert all(p.records[0].verification.text_verified for p in result.hba1c_observations)


async def test_service_reuses_loader_reauthorizes_and_does_not_commit(monkeypatch):
    loader = AsyncMock(return_value=([lab()], [lab()]))
    authorize = AsyncMock()
    monkeypatch.setattr(service, "load_accepted_state", loader)
    monkeypatch.setattr(service, "authorize_patient_scope", authorize)
    async with AsyncSession() as db:
        db.commit, db.flush, db.rollback = AsyncMock(), AsyncMock(), AsyncMock()
        run = await service.summarize_patient_records(db, AUTHOR, PATIENT)
        loader.assert_awaited_once_with(db, AUTHOR, PATIENT)
        authorize.assert_awaited_once_with(db, AUTHOR, PATIENT)
        assert run.summary.latest_hba1c.value == Decimal("7.1")
        db.commit.assert_not_awaited()
        db.flush.assert_not_awaited()
        db.rollback.assert_not_awaited()
        assert not db.new and not db.dirty and not db.deleted


@pytest.mark.parametrize("stage", ["load_accepted_state", "authorize_patient_scope"])
async def test_unauthorized_or_revoked_access_withholds_summary(monkeypatch, stage):
    monkeypatch.setattr(service, "load_accepted_state", AsyncMock(return_value=([lab()], [lab()])))
    monkeypatch.setattr(service, "authorize_patient_scope", AsyncMock())
    monkeypatch.setattr(
        service, stage, AsyncMock(side_effect=HTTPException(403, "Patient scope denied"))
    )
    async with AsyncSession() as db:
        with pytest.raises(HTTPException) as error:
            await service.summarize_patient_records(db, AUTHOR, PATIENT)
        assert error.value.status_code == 403


async def test_service_integrity_failure_has_generic_error(monkeypatch):
    monkeypatch.setattr(
        service, "load_accepted_state", AsyncMock(return_value=([lab(status="PENDING")], []))
    )
    authorize = AsyncMock()
    monkeypatch.setattr(service, "authorize_patient_scope", authorize)
    async with AsyncSession() as db:
        with pytest.raises(HTTPException) as error:
            await service.summarize_patient_records(db, AUTHOR, PATIENT)
        assert error.value.status_code == 409
        assert error.value.detail == "Accepted summary context unavailable"
        authorize.assert_not_awaited()


async def test_postgresql_read_only_current_accepted_snapshot(db, api):
    patient, profile = await add_patient(db)
    response = await api.post(path(profile.id), headers=headers(patient), json=entry())
    assert response.status_code == 201
    rows = list(await db.scalars(select(ClinicalFactVersion)))
    before = [version_input(r).model_dump_json() for r in rows]
    decisions = list(await db.scalars(select(ConfirmationDecision.proposal_id)))
    run = await service.summarize_patient_records(db, patient.id, profile.id)
    assert run.summary.latest_hba1c.value == Decimal("7.1")
    assert [
        version_input(r).model_dump_json() for r in await db.scalars(select(ClinicalFactVersion))
    ] == before
    assert list(await db.scalars(select(ConfirmationDecision.proposal_id))) == decisions
    assert not db.new and not db.dirty and not db.deleted
    assert db.in_transaction()
    await db.rollback()


async def test_postgresql_consent_revocation_and_patient_isolation(db, api):
    patient, profile = await add_patient(db)
    doctor, practitioner = await add_practitioner(db)
    actor_id, patient_id, practitioner_id = doctor.id, profile.id, practitioner.id
    await db.commit()
    with pytest.raises(HTTPException) as error:
        await service.summarize_patient_records(db, actor_id, patient_id)
    assert error.value.status_code == 403
    await db.rollback()
    await db.refresh(patient)
    assert (await grant(api, patient, practitioner_id)).status_code == 200
    assert (
        await service.summarize_patient_records(db, actor_id, patient_id)
    ).summary.patient_id == patient_id
    await db.rollback()
    await db.refresh(patient)
    assert (await revoke(api, patient, practitioner_id)).status_code == 200
    with pytest.raises(HTTPException) as error:
        await service.summarize_patient_records(db, actor_id, patient_id)
    assert error.value.status_code == 403
    await db.rollback()
    await db.refresh(patient)
    _, other = await add_patient(db)
    with pytest.raises(HTTPException) as error:
        await service.summarize_patient_records(db, patient.id, other.id)
    assert error.value.status_code == 403
