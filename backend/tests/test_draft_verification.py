"""Independent record restatement; arbitrary prose is never verified."""

import asyncio
import json
import secrets
from datetime import timedelta, timezone
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import decode_access_token
from app.models import ClinicalFactVersion, ConfirmationDecision
from app.schemas.draft_verification import DraftClaim, DraftVerificationRequest
from app.services import draft_verification as service
from app.services.consistency import version_input
from app.services.consistency_evaluator import ConsistencyInputError
from app.services.draft_verifier import DraftInputError, verify_fact_claims
from app.services.fact_renderer import render_restatement
from tests.test_auth import add_patient, add_practitioner, headers
from tests.test_clinical_facts import candidate, entry, path, wait_until_blocked
from tests.test_clinical_facts import race_environment as race_environment
from tests.test_consents import grant, revoke
from tests.test_consistency import AUTHOR, PATIENT, WHEN, correction, fact


@pytest.fixture(autouse=True)
def signing_configuration(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", secrets.token_urlsafe(48))


def claim_for(row, **changes):
    values = dict(
        claim_id=UUID(int=100),
        patient_id=row.patient_id,
        claim_type="FACT_RESTATEMENT",
        fact_version_ids=(row.version_id,),
        source_attribution=dict(
            author_id=row.author_id, author_role=row.author_role, category=row.attribution
        ),
        proposed_content=dict(fact=row.fact.model_dump(), observed_at=row.observed_at),
    )
    values.update(changes)
    return DraftClaim.model_validate(values)


def request_for(*claims, patient_id=PATIENT, **changes):
    return DraftVerificationRequest.model_validate(
        dict(draft_id=UUID(int=200), patient_id=patient_id, claims=claims, **changes)
    )


def checked(row, claim=None):
    return verify_fact_claims(
        row.patient_id,
        [row],
        [row],
        request_for(claim or claim_for(row), patient_id=row.patient_id),
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "laboratory", "code": "hba1c", "value": "7.1", "unit": "%"},
        {"kind": "diagnosis", "code": "type_2_diabetes"},
        {
            "kind": "medication",
            "medication_name": "synthetic metformin",
            "dose_value": "500.00",
            "dose_unit": "mg",
            "frequency": "recorded frequency",
        },
        {"kind": "symptom", "code": "fatigue", "description": "synthetic report"},
        {"kind": "history", "category": "family_history", "description": "synthetic history"},
    ],
)
def test_all_existing_fact_types_exact_record_restated(payload):
    row = fact(fact=payload)
    result = checked(row).claims[0]
    assert result.outcome == "GROUNDED" and result.text_verified
    assert result.reason_code == "FACT_RESTATEMENT_MATCH"
    assert result.rendered_text == render_restatement(row)
    assert result.rendered_text.startswith("Recorded patient_reported")
    assert not checked(row).unrestricted_text_verified


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        (
            {"kind": "symptom", "code": "fatigue", "description": "synthetic report"},
            "FACT_KIND_MISMATCH",
        ),
        (
            {"kind": "laboratory", "code": "glucose", "value": "110", "unit": "mg/dL"},
            "FACT_CODE_MISMATCH",
        ),
        (
            {"kind": "laboratory", "code": "hba1c", "value": "7.1", "unit": "mmol/mol"},
            "FACT_UNIT_MISMATCH",
        ),
        (
            {"kind": "laboratory", "code": "hba1c", "value": "8.2", "unit": "%"},
            "FACT_VALUE_MISMATCH",
        ),
    ],
)
def test_independent_payload_mismatches(changes, reason):
    row = fact()
    claim = claim_for(row, proposed_content=dict(fact=changes, observed_at=row.observed_at))
    result = checked(row, claim).claims[0]
    assert result.outcome == "UNGROUNDED" and result.reason_code == reason
    assert not result.text_verified and result.rendered_text is None


@pytest.mark.parametrize("field", ["author_id", "author_role", "category"])
def test_attribution_cannot_be_promoted_or_changed(field):
    row = fact()
    data = claim_for(row).model_dump()
    data["source_attribution"][field] = {
        "author_id": uuid4(),
        "author_role": "practitioner",
        "category": "practitioner_managed",
    }[field]
    result = checked(row, DraftClaim.model_validate(data)).claims[0]
    assert result.outcome == "UNGROUNDED" and result.reason_code == "SOURCE_ATTRIBUTION_MISMATCH"
    assert not result.text_verified


def test_observation_time_mismatch_and_timezone_decimal_equivalence():
    row = fact()
    changed = claim_for(
        row,
        proposed_content=dict(fact=row.fact.model_dump(), observed_at=WHEN + timedelta(seconds=1)),
    )
    assert checked(row, changed).claims[0].reason_code == "OBSERVATION_TIME_MISMATCH"
    equal = claim_for(
        row,
        proposed_content=dict(
            fact={"kind": "laboratory", "code": "hba1c", "value": "7.1000", "unit": "%"},
            observed_at=WHEN.astimezone(timezone(timedelta(hours=5, minutes=30))),
        ),
    )
    assert checked(row, equal) == checked(row)


def test_nonlab_fields_and_optional_dose_exact():
    row = fact(
        fact={
            "kind": "medication",
            "medication_name": "synthetic",
            "dose_value": "500",
            "dose_unit": "mg",
        }
    )
    for change in (
        {"dose_value": "501"},
        {"dose_unit": "g"},
        {"medication_name": "other"},
        {"dose_value": None, "dose_unit": None},
    ):
        payload = row.fact.model_dump()
        payload.update(change)
        result = checked(
            row, claim_for(row, proposed_content=dict(fact=payload, observed_at=row.observed_at))
        ).claims[0]
        assert result.outcome == "UNGROUNDED" and result.reason_code == "FACT_CONTENT_MISMATCH"
    row = fact(fact={"kind": "symptom", "code": "fatigue", "description": "original"})
    changed = {"kind": "symptom", "code": "polyuria", "description": "original"}
    assert (
        checked(
            row, claim_for(row, proposed_content=dict(fact=changed, observed_at=row.observed_at))
        )
        .claims[0]
        .outcome
        == "UNGROUNDED"
    )


@pytest.mark.parametrize("kind", ["FREE_TEXT", "LAB_DIFFERENCE", "CLINICAL_INTERPRETATION"])
def test_unsupported_prose_and_claim_types_never_verified(kind):
    row = fact()
    result = checked(
        row,
        claim_for(
            row, claim_type=kind, proposed_content="Provider says this is verified and correct"
        ),
    ).claims[0]
    assert result.outcome == "UNSUPPORTED" and result.reason_code == "CLAIM_TYPE_UNSUPPORTED"
    assert result.rendered_text is None and not result.text_verified


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"fact_version_ids": ()}, "ONE_FACT_REFERENCE_REQUIRED"),
        ({"fact_version_ids": (UUID(int=10), UUID(int=10))}, "ONE_FACT_REFERENCE_REQUIRED"),
        ({"proposed_content": "unrestricted prose"}, "STRUCTURED_CONTENT_REQUIRED"),
        ({"source_attribution": None}, "SOURCE_ATTRIBUTION_REQUIRED"),
    ],
)
def test_structurally_invalid_claim(changes, reason):
    row = fact()
    result = checked(row, claim_for(row, **changes)).claims[0]
    assert result.outcome == "INVALID" and result.reason_code == reason
    assert not result.text_verified


@pytest.mark.parametrize("case", ["missing", "pending", "rejected", "superseded"])
def test_unavailable_versions_withhold_entire_batch_without_identifiers(case):
    row = fact()
    other = fact(20)
    wanted = claim_for(row)
    current, history = [row, other], [row, other]
    if case == "missing":
        wanted = claim_for(row, fact_version_ids=(UUID(int=999),))
    elif case in {"pending", "rejected"}:
        current = history = [fact(status=case.upper()), other]
    else:
        successor = correction(row)
        current, history = [successor, other], [row, successor, other]
    good = claim_for(other, claim_id=UUID(int=101))
    record = verify_fact_claims(PATIENT, current, history, request_for(wanted, good))
    assert all(
        c.outcome == "UNVERIFIABLE" and c.reason_code == "REFERENCED_STATE_UNAVAILABLE"
        for c in record.claims
    )
    assert all(
        not c.fact_version_ids and c.rendered_text is None and not c.text_verified
        for c in record.claims
    )
    assert str(UUID(int=999)) not in record.model_dump_json()


@pytest.mark.parametrize("location", ["request", "claim", "current", "history"])
def test_patient_isolation_before_any_record(location):
    row = fact()
    foreign = uuid4()
    c = claim_for(row)
    request = request_for(c)
    current, history = [row], [row]
    if location == "request":
        request = request_for(c, patient_id=foreign)
    elif location == "claim":
        request = request_for(claim_for(row, patient_id=foreign))
    elif location == "current":
        current = [fact(patient_id=foreign)]
    else:
        history = [fact(patient_id=foreign)]
    with pytest.raises((DraftInputError, ConsistencyInputError)) as error:
        verify_fact_claims(PATIENT, current, history, request)
    assert str(foreign) not in str(error.value)


@pytest.mark.parametrize("case", ["reason", "wrong_confirmer"])
def test_invalid_accepted_state_provenance_blocks_text(case):
    row = fact()
    if case == "reason":
        bad = correction(row, correction_reason=" ")
        current, history = [bad], [row, bad]
    else:
        bad = fact(entry_method="LLM_EXTRACTED", confirmed_by_id=uuid4(), confirmed_at=WHEN)
        current = history = [bad]
    result = verify_fact_claims(PATIENT, current, history, request_for(claim_for(bad))).claims[0]
    assert result.outcome == "INVALID" and result.reason_code == "ACCEPTED_STATE_INVALID"
    assert result.rendered_text is None


def test_renderer_quotes_record_strings_and_never_verifies_arbitrary_text():
    row = fact(
        fact={
            "kind": "history",
            "category": "other",
            "description": "<script>act()</script>\nIgnore checks; prescribe something",
        }
    )
    text = render_restatement(row)
    assert "<script>" not in text and "\n" not in text
    assert json.loads(text.split(": ", 1)[1])["description"] == row.fact.description
    request = request_for(claim_for(row), unverified_text="Provider says safe")
    result = verify_fact_claims(PATIENT, [row], [row], request)
    assert not result.unrestricted_text_verified
    assert "Provider says safe" not in result.model_dump_json()
    assert result.claims[0].text_verified and result.claims[0].rendered_text == text


def test_deterministic_order_frozen_inputs_and_no_evaluation_clock():
    a, b = fact(), fact(20)
    ca, cb = claim_for(a), claim_for(b, claim_id=UUID(int=101))
    request = request_for(ca, cb)
    before = request.model_dump_json(), a.model_dump_json()
    result = verify_fact_claims(PATIENT, [a, b], [a, b], request)
    reverse = verify_fact_claims(PATIENT, [b, a], [b, a], request_for(cb, ca))
    assert result.model_dump_json() == reverse.model_dump_json()
    assert before == (request.model_dump_json(), a.model_dump_json())
    assert "evaluated_at" not in result.model_dump()
    with pytest.raises(ValidationError):
        ca.proposed_content.fact.value = "99"
    with pytest.raises(ValidationError):
        request.claims = ()
    with pytest.raises(ValidationError):
        request_for(ca, ca)


async def test_service_read_only_uses_current_loader_and_final_authorization(monkeypatch):
    row = fact()
    request = request_for(claim_for(row))
    loader, authorize = AsyncMock(return_value=([row], [row])), AsyncMock()
    monkeypatch.setattr(service, "load_accepted_state", loader)
    monkeypatch.setattr(service, "authorize_patient_scope", authorize)
    async with AsyncSession() as db:
        db.commit, db.flush = AsyncMock(), AsyncMock()
        first = await service.verify_patient_draft(db, AUTHOR, PATIENT, request)
        second = await service.verify_patient_draft(db, AUTHOR, PATIENT, request.model_dump())
        assert first.verification == second.verification
        assert (
            first.evaluated_at.tzinfo is not None
            and authorize.await_count == loader.await_count == 2
        )
        db.commit.assert_not_awaited()
        db.flush.assert_not_awaited()
        authorize.side_effect = HTTPException(403, "Patient scope denied")
        with pytest.raises(HTTPException) as error:
            await service.verify_patient_draft(db, AUTHOR, PATIENT, request)
        assert error.value.status_code == 403


@pytest.mark.parametrize(
    "case", ["verified_flag", "confirmed_flag", "invalid_time", "duplicate_id", "malformed_model"]
)
async def test_malformed_requests_sanitized_and_provider_assertions_not_trusted(monkeypatch, case):
    row = fact()
    request = request_for(claim_for(row))
    data = request.model_dump()
    if case == "verified_flag":
        data["verified"] = True
    elif case == "confirmed_flag":
        data["claims"][0]["proposed_content"]["confirmed"] = True
    elif case == "invalid_time":
        data["claims"][0]["proposed_content"]["observed_at"] = "secret malformed timestamp"
    elif case == "duplicate_id":
        data["claims"] = (data["claims"][0], data["claims"][0])
    else:
        data = request.model_copy(update={"patient_id": "secret invalid identity"})
    monkeypatch.setattr(service, "load_accepted_state", AsyncMock(return_value=([row], [row])))
    async with AsyncSession() as db:
        with pytest.raises(HTTPException) as error:
            await service.verify_patient_draft(db, AUTHOR, PATIENT, data)
    assert (
        error.value.status_code == 422
        and error.value.detail == "Invalid draft verification request"
    )


async def test_service_denies_scope_without_parsing_untrusted_draft(monkeypatch):
    monkeypatch.setattr(
        service,
        "load_accepted_state",
        AsyncMock(side_effect=HTTPException(403, "Patient scope denied")),
    )
    async with AsyncSession() as db:
        with pytest.raises(HTTPException) as error:
            await service.verify_patient_draft(db, AUTHOR, PATIENT, {"confirmed": True})
        assert error.value.status_code == 403


async def test_database_current_versions_read_only_and_missing_state(db, api):
    patient, profile = await add_patient(db)
    account_id, patient_id = patient.id, profile.id
    first = (await api.post(path(patient_id), headers=headers(patient), json=entry())).json()
    original = await db.get(ClinicalFactVersion, UUID(first["version_id"]))
    before = version_input(original)
    pending, rejected = (
        candidate(patient_id, account_id),
        candidate(patient_id, account_id, status="REJECTED"),
    )
    db.add_all([pending, rejected])
    await db.commit()
    successor = (
        await api.post(
            f"{path(patient_id)}/{first['fact_id']}/corrections",
            headers=headers(patient),
            json={
                **entry("7.3"),
                "expected_version_id": first["version_id"],
                "correction_reason": "synthetic correction",
            },
        )
    ).json()
    current = version_input(await db.get(ClinicalFactVersion, UUID(successor["version_id"])))
    rows_before = {
        r.id: version_input(r).model_dump_json()
        for r in await db.scalars(select(ClinicalFactVersion))
    }
    request = request_for(claim_for(current), patient_id=patient_id)
    result = await service.verify_patient_draft(db, account_id, patient_id, request)
    assert result.verification.claims[0].outcome == "GROUNDED" and db.in_transaction()
    for missing in (pending.id, rejected.id, before.version_id, uuid4()):
        stale = request_for(claim_for(before, fact_version_ids=(missing,)), patient_id=patient_id)
        blocked = await service.verify_patient_draft(db, account_id, patient_id, stale)
        assert blocked.verification.claims[0].outcome == "UNVERIFIABLE"
        assert blocked.verification.claims[0].fact_version_ids == ()
    assert not db.new and not db.dirty and not db.deleted
    assert rows_before == {
        r.id: version_input(r).model_dump_json()
        for r in await db.scalars(select(ClinicalFactVersion))
    }
    await db.rollback()


async def test_database_foreign_scope_and_reference(db, api):
    patient, profile = await add_patient(db)
    other, other_profile = await add_patient(db)
    account_id, patient_id, other_id = patient.id, profile.id, other_profile.id
    response = (await api.post(path(other_id), headers=headers(other), json=entry())).json()
    foreign = version_input(await db.get(ClinicalFactVersion, UUID(response["version_id"])))
    with pytest.raises(HTTPException) as error:
        await service.verify_patient_draft(
            db, account_id, other_id, request_for(claim_for(foreign), patient_id=other_id)
        )
    assert error.value.status_code == 403
    hidden = claim_for(foreign, patient_id=patient_id)
    result = await service.verify_patient_draft(
        db, account_id, patient_id, request_for(hidden, patient_id=patient_id)
    )
    assert result.verification.claims[0].outcome == "UNVERIFIABLE"
    assert str(foreign.version_id) not in result.model_dump_json()
    await db.rollback()


async def test_database_practitioner_current_consent(db, api):
    patient, profile = await add_patient(db)
    doctor, practitioner = await add_practitioner(db)
    account_id, patient_id, practitioner_id = doctor.id, profile.id, practitioner.id
    await db.commit()
    request = request_for(patient_id=patient_id)
    with pytest.raises(HTTPException) as error:
        await service.verify_patient_draft(db, account_id, patient_id, request)
    assert error.value.status_code == 403
    await db.rollback()
    await db.refresh(patient)
    assert (await grant(api, patient, practitioner_id)).status_code == 200
    await service.verify_patient_draft(db, account_id, patient_id, request)
    await db.rollback()
    await db.refresh(patient)
    assert (await revoke(api, patient, practitioner_id)).status_code == 200
    with pytest.raises(HTTPException) as error:
        await service.verify_patient_draft(db, account_id, patient_id, request)
    assert error.value.status_code == 403
    await db.rollback()


async def test_database_confirmation_history_unchanged(db, api):
    from tests.test_extraction import decide, extract

    patient, profile = await add_patient(db)
    account_id, patient_id = patient.id, profile.id
    auth = headers(patient)
    items = (await extract(api, patient_id, auth)).json()["confirmations"]
    assert (await decide(api, patient_id, auth, items[0]["confirmation_id"])).status_code == 200
    decision = await db.get(ConfirmationDecision, UUID(items[0]["confirmation_id"]))
    before = (decision.status, decision.decided_by_id, decision.accepted_version_id)
    row = version_input(await db.get(ClinicalFactVersion, decision.accepted_version_id))
    result = await service.verify_patient_draft(
        db, account_id, patient_id, request_for(claim_for(row), patient_id=patient_id)
    )
    assert result.verification.claims[0].outcome == "GROUNDED"
    assert before == (decision.status, decision.decided_by_id, decision.accepted_version_id)
    assert decision.decided_by_id == account_id
    await db.rollback()


@pytest.mark.parametrize("revoke_first", [True, False])
async def test_database_revocation_and_protected_consumption(db, race_environment, revoke_first):
    env = race_environment
    env["hold_revoke"] = revoke_first
    account_id = decode_access_token(env["doctor_auth"]["Authorization"].split()[1])
    stored = await db.scalar(
        select(ClinicalFactVersion).where(ClinicalFactVersion.patient_id == env["patient_id"])
    )
    row = version_input(stored)
    request = request_for(claim_for(row), patient_id=env["patient_id"])
    async with (
        AsyncClient(transport=ASGITransport(app=env["app"]), base_url="http://test") as client,
        AsyncSession(env["engine"], expire_on_commit=False) as session,
    ):

        async def check():
            env["pids"]["clinical"] = await session.scalar(text("SELECT pg_backend_pid()"))
            return await service.verify_patient_draft(
                session, account_id, env["patient_id"], request
            )

        async def revocation():
            return await client.post(
                f"/api/v1/patients/me/consents/{env['doctor_profile_id']}/revoke",
                headers=env["auth"],
            )

        if revoke_first:
            revoking = asyncio.create_task(revocation())
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
            verified = await check()
            assert verified.verification.claims[0].outcome == "GROUNDED"
            assert verified.verification.claims[0].text_verified
            revoking = asyncio.create_task(revocation())
            try:
                await wait_until_blocked(db, env, "revoke")
                assert not revoking.done()
            finally:
                await session.rollback()
            assert (await revoking).status_code == 200


async def test_database_correction_waits_for_consumption_and_stale_claim_is_not_rebound(
    db, race_environment
):
    env = race_environment
    patient_id = env["patient_id"]
    account_id = decode_access_token(env["doctor_auth"]["Authorization"].split()[1])
    stored = await db.scalar(
        select(ClinicalFactVersion).where(ClinicalFactVersion.patient_id == patient_id)
    )
    row = version_input(stored)
    request = request_for(claim_for(row), patient_id=patient_id)
    async with (
        AsyncSession(env["engine"], expire_on_commit=False) as session,
        AsyncClient(transport=ASGITransport(app=env["app"]), base_url="http://test") as client,
    ):
        verified = await service.verify_patient_draft(session, account_id, patient_id, request)
        assert verified.verification.claims[0].outcome == "GROUNDED"
        correcting = asyncio.create_task(
            client.post(
                f"{path(patient_id)}/{row.fact_id}/corrections",
                headers=env["auth"],
                json={
                    **entry("7.4"),
                    "expected_version_id": str(row.version_id),
                    "correction_reason": "synthetic correction",
                },
            )
        )
        try:
            await wait_until_blocked(db, env, "clinical")
            assert not correcting.done()
        finally:
            await session.rollback()
        assert (await correcting).status_code == 201
        stale = await service.verify_patient_draft(session, account_id, patient_id, request)
        assert stale.verification.claims[0].outcome == "UNVERIFIABLE"
        assert stale.verification.claims[0].rendered_text is None
        await session.rollback()


def test_identical_provider_text_is_still_not_the_verified_channel():
    row = fact()
    request = request_for(claim_for(row), unverified_text=render_restatement(row))
    record = verify_fact_claims(PATIENT, [row], [row], request)
    assert not record.unrestricted_text_verified
    assert record.claims[0].text_verified
    assert record.claims[0].rendered_text == render_restatement(row)
