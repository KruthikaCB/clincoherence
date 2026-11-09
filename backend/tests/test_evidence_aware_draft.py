"""Synthetic-only composition; grounding is independent of evidence and prose."""

import asyncio
import secrets
from dataclasses import replace
from unittest.mock import AsyncMock, Mock
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
from app.schemas.evidence_aware_draft import ClaimEvidenceBinding, EvidenceAwareDraftRequest
from app.services import evidence_aware_draft as service
from app.services import evidence_catalog as catalog_module
from app.services.consistency import version_input
from app.services.consistency_evaluator import ConsistencyInputError
from app.services.draft_verifier import DraftInputError
from app.services.evidence_aware_evaluator import (
    evaluate_evidence_aware_draft,
    evidence_request,
    prepare_batch,
)
from app.services.evidence_catalog import load_catalog, resolve_catalog
from tests.test_auth import add_patient, add_practitioner, headers
from tests.test_clinical_facts import candidate, entry, path, wait_until_blocked
from tests.test_clinical_facts import race_environment as race_environment
from tests.test_consents import grant, revoke
from tests.test_consistency import AUTHOR, PATIENT, correction, fact
from tests.test_draft_verification import claim_for, request_for
from tests.test_evidence import altered
from tests.test_evidence import request_for as evidence_fixture_request


@pytest.fixture(autouse=True)
def signing_configuration(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", secrets.token_urlsafe(48))


def binding_for(claim, **changes):
    fixture = evidence_fixture_request()
    return ClaimEvidenceBinding.model_validate(
        dict(
            claim_id=claim.claim_id,
            rule=fixture.rules[0],
            approval_manifest_id=fixture.approval_manifest_id,
            approval_manifest_version=fixture.approval_manifest_version,
            intended_use=fixture.intended_use,
            **changes,
        )
    )


def composed(*claims, bound=True, **changes):
    return EvidenceAwareDraftRequest(
        draft=request_for(*claims, patient_id=claims[0].patient_id if claims else PATIENT),
        bindings=tuple(binding_for(c) for c in claims) if bound else (),
        **changes,
    )


def evaluate(request, current, history=None, prepared=None):
    return evaluate_evidence_aware_draft(
        request.draft.patient_id,
        current,
        current if history is None else history,
        request,
        prepare_batch(request) if prepared is None else prepared,
    )


def test_grounding_integrity_applicability_and_text_are_separate():
    row = fact()
    request = composed(claim_for(row))
    record = evaluate(request, [row])
    result = record.claims[0]
    assert result.grounding.outcome == "GROUNDED"
    assert result.evidence.integrity.reason_code == "SYNTHETIC_EVIDENCE_RESOLVED"
    assert result.evidence.applicability.reason_code == "SYNTHETIC_CONTEXT_MATCHED"
    assert result.evidence.applicability.matched_fact_version_ids == (row.version_id,)
    assert result.rendered_text and result.text_verified
    assert "rendered_text" not in result.grounding.model_dump()
    assert not record.unrestricted_text_verified
    assert record.snapshot.current_version_ids == (row.version_id,)
    assert "evaluated_at" not in record.model_dump()


def test_no_binding_is_explicit_4d_only():
    row = fact()
    result = evaluate(composed(claim_for(row), bound=False), [row]).claims[0]
    assert result.evidence is None and result.reason_code == "EVIDENCE_NOT_REQUESTED"
    assert result.text_verified


def test_missing_claim_context_is_invalid_not_fabricated_applicability():
    row = fact()
    result = evaluate(composed(claim_for(row, fact_version_ids=())), [row]).claims[0]
    assert result.grounding.reason_code == "ONE_FACT_REFERENCE_REQUIRED"
    assert result.evidence.applicability is None and not result.text_verified


def test_foreign_reference_is_not_echoed_and_other_claim_text_is_withheld():
    row = fact()
    foreign = fact(20, patient_id=uuid4())
    request = composed(
        claim_for(row), claim_for(foreign, patient_id=PATIENT, claim_id=UUID(int=101))
    )
    record = evaluate(request, [row])
    assert str(foreign.version_id) not in record.model_dump_json()
    assert str(foreign.patient_id) not in record.model_dump_json()
    assert all(not c.text_verified for c in record.claims)


@pytest.mark.parametrize(
    "field",
    [
        "source_id",
        "source_version",
        "source_edition",
        "source_content_hash",
        "unit_id",
        "unit_version",
        "unit_content_hash",
    ],
)
def test_wrong_reference_withholds_only_bound_claim(field):
    row, other = fact(), fact(20)
    ca, cb = claim_for(row), claim_for(other, claim_id=UUID(int=101))
    binding = binding_for(ca)
    ref = binding.rule.evidence[0].model_copy(
        update={
            field: "0" * 64 if "hash" in field else "wrong",
        }
    )
    binding = binding.model_copy(
        update={"rule": binding.rule.model_copy(update={"evidence": (ref,)})}
    )
    request = EvidenceAwareDraftRequest(draft=request_for(ca, cb), bindings=(binding,))
    results = evaluate(request, [row, other]).claims
    assert results[0].grounding.outcome == "GROUNDED"
    assert results[0].evidence.integrity.status == "UNAVAILABLE"
    assert results[0].evidence.integrity.reason_code == "EVIDENCE_BINDING_MISMATCH"
    assert not results[0].text_verified and results[0].rendered_text is None
    assert results[1].text_verified


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("approval_manifest_version", "wrong", "MANIFEST_VERSION_MISMATCH"),
        ("approval_manifest_id", "wrong", "MANIFEST_VERSION_MISMATCH"),
        ("intended_use", "clinical-care", "INTENDED_USE_NOT_APPROVED"),
    ],
)
def test_manifest_and_intended_use_fail_closed(field, value, reason):
    row = fact()
    claim = claim_for(row)
    binding = binding_for(claim).model_copy(update={field: value})
    request = EvidenceAwareDraftRequest(draft=request_for(claim), bindings=(binding,))
    result = evaluate(request, [row]).claims[0]
    assert result.grounding.outcome == "GROUNDED" and not result.text_verified
    assert result.evidence.integrity.reason_code == reason
    assert result.evidence.applicability is None


@pytest.mark.parametrize("withdrawn", [True, False])
def test_reviewed_withdrawal_or_missing_approval_preserves_4c_reasons(withdrawn):
    row = fact()
    request = composed(claim_for(row))
    catalog = load_catalog()
    approvals = catalog.manifest.approvals
    approvals = (
        (approvals[0].model_copy(update={"status": "withdrawn"}), *approvals[1:])
        if withdrawn
        else approvals[1:]
    )
    trusted_fixture = altered(
        catalog,
        manifest=catalog.manifest.model_copy(
            update={
                "approvals": approvals,
            }
        ),
    )
    # Simulates a reviewed deployment; production never accepts a supplied catalogue.
    prepared = prepare_batch(request)
    prepared = replace(
        prepared,
        evidence=(
            (
                request.bindings[0].claim_id,
                resolve_catalog(
                    evidence_request(request, request.bindings[0]),
                    trusted_fixture,
                ),
            ),
        ),
    )
    result = evaluate(request, [row], prepared=prepared).claims[0]
    assert result.evidence.integrity.reason_code == (
        "APPROVAL_WITHDRAWN" if withdrawn else "EVIDENCE_NOT_APPROVED"
    )
    assert not result.text_verified and result.rendered_text is None


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "laboratory", "code": "hba1c", "value": "7.1", "unit": "mmol/mol"},
        {"kind": "laboratory", "code": "glucose", "value": "90", "unit": "mg/dL"},
        {"kind": "symptom", "code": "fatigue", "description": "synthetic"},
    ],
)
def test_incompatible_context_does_not_change_grounding(payload):
    row = fact(fact=payload)
    result = evaluate(composed(claim_for(row)), [row]).claims[0]
    assert result.grounding.outcome == "GROUNDED"
    assert result.evidence.integrity.status == "RESOLVED"
    assert result.evidence.applicability.status == "NOT_APPLICABLE"
    assert not result.text_verified


def test_qualifier_mismatch():
    row = fact(author_role="practitioner", attribution="practitioner_managed")
    result = evaluate(composed(claim_for(row)), [row]).claims[0]
    assert result.evidence.applicability.reason_code == "STRUCTURED_QUALIFIER_MISMATCH"
    assert not result.text_verified


@pytest.mark.parametrize("field", ["approved", "patient_id", "fact_version_ids", "outcome"])
def test_provider_cannot_supply_approval_outcome_or_independent_context(field):
    data = composed(claim_for(fact())).model_dump()
    data["bindings"][0][field] = True
    with pytest.raises(ValidationError):
        EvidenceAwareDraftRequest.model_validate(data)


@pytest.mark.parametrize("unknown", [False, True])
def test_bindings_require_unique_existing_claims(unknown):
    request = composed(claim_for(fact()))
    bindings = (
        (request.bindings[0].model_copy(update={"claim_id": uuid4()}),)
        if unknown
        else request.bindings * 2
    )
    with pytest.raises(ValidationError):
        EvidenceAwareDraftRequest(draft=request.draft, bindings=bindings)


@pytest.mark.parametrize(
    "changes",
    [
        {"proposed_content": "Take medication; provider says approved"},
        {"claim_type": "CLINICAL_INTERPRETATION"},
        {"claim_type": "LAB_DIFFERENCE"},
    ],
)
def test_prose_and_interpretations_never_verified(changes):
    row = fact()
    result = evaluate(composed(claim_for(row, **changes)), [row]).claims[0]
    assert result.grounding.outcome in {"INVALID", "UNSUPPORTED"}
    assert result.reason_code == "CLAIM_NOT_GROUNDED"
    assert result.evidence.applicability is None and not result.text_verified


def test_renderer_matching_provider_prose_is_still_unverified():
    from app.services.fact_renderer import render_restatement

    row = fact()
    request = composed(claim_for(row, proposed_content=render_restatement(row)))
    assert not evaluate(request, [row]).claims[0].text_verified


@pytest.mark.parametrize("status", ["PENDING", "REJECTED", "MISSING", "SUPERSEDED"])
def test_unavailable_versions_withhold_entire_batch_and_hide_identifiers(status):
    a, b = fact(), fact(20)
    current, history = [a], [a]
    if status in {"PENDING", "REJECTED"}:
        b = b.model_copy(update={"status": status})
    if status == "SUPERSEDED":
        successor = correction(b, 21)
        current, history = [a, successor], [a, b, successor]
    request = composed(claim_for(a), claim_for(b, claim_id=UUID(int=101)))
    result = evaluate(request, current, history)
    assert all(not c.text_verified for c in result.claims)
    assert all(c.grounding.reason_code == "REFERENCED_STATE_UNAVAILABLE" for c in result.claims)
    assert all(c.grounding.fact_version_ids == () for c in result.claims)
    # Authorized accepted history remains in the reproducible snapshot; only
    # inaccessible/proposal references must never be echoed there.
    if status != "SUPERSEDED":
        assert str(b.version_id) not in result.model_dump_json()


def test_scope_and_invalid_provenance_block_batch():
    row = fact()
    request = composed(claim_for(row, patient_id=uuid4()))
    with pytest.raises((DraftInputError, ConsistencyInputError)):
        evaluate(request, [row])
    invalid = fact(entry_method="LLM_EXTRACTED", confirmed_by_id=None, confirmed_at=None)
    record = evaluate(composed(claim_for(invalid)), [invalid])
    assert record.claims[0].reason_code == "ACCEPTED_STATE_INVALID"
    assert not record.claims[0].text_verified


@pytest.mark.parametrize("filename", ["bundle.json", "rules.json", "approvals.json"])
def test_catalogue_tampering_withholds_even_unbound_text(tmp_path, monkeypatch, filename):
    row = fact()
    request = composed(claim_for(row), bound=False)
    for name in catalog_module.PINS:
        raw = (catalog_module.ASSETS / name).read_text(encoding="utf-8")
        (tmp_path / name).write_text(
            raw + ("invalid" if name == filename else ""), encoding="utf-8"
        )
    monkeypatch.setattr(catalog_module, "ASSETS", tmp_path)
    record = evaluate(request, [row])
    assert record.catalogue_fingerprint is None
    assert record.claims[0].reason_code == "CATALOG_UNAVAILABLE_OR_INVALID"
    assert record.claims[0].rendered_text is None


def test_determinism_immutable_inputs_and_fixed_codes():
    a, b = fact(), fact(20)
    ca, cb = claim_for(a), claim_for(b, claim_id=UUID(int=101))
    request = composed(ca, cb)
    before = request.model_dump_json(), a.model_dump_json()
    first = evaluate(request, [a, b])
    assert first == evaluate(composed(cb, ca), [b, a])
    assert before == (request.model_dump_json(), a.model_dump_json())
    with pytest.raises(ValidationError):
        request.bindings[0].intended_use = "clinical-care"
    assert {c.reason_code for c in first.claims} == {"SYNTHETIC_CONTEXT_MATCHED"}


async def test_service_and_guarded_consumption_are_read_only(monkeypatch):
    row = fact()
    request = composed(claim_for(row))
    monkeypatch.setattr(service, "load_accepted_state", AsyncMock(return_value=([row], [row])))
    authorize = AsyncMock()
    monkeypatch.setattr(service, "authorize_patient_scope", authorize)
    async with AsyncSession() as db:
        db.commit, db.flush = AsyncMock(), AsyncMock()
        run = await service.verify_patient_evidence_aware_draft(db, AUTHOR, PATIENT, request)
        consumer = Mock(return_value="consumed")
        assert (
            await service.consume_patient_evidence_aware_draft(
                db,
                AUTHOR,
                PATIENT,
                request,
                run.verification,
                consumer,
            )
            == "consumed"
        )
        consumer.assert_called_once_with(run.verification)
        db.commit.assert_not_awaited()
        db.flush.assert_not_awaited()
        authorize.side_effect = HTTPException(403, "Patient scope denied")
        with pytest.raises(HTTPException):
            await service.consume_patient_evidence_aware_draft(
                db,
                AUTHOR,
                PATIENT,
                request,
                run.verification,
                consumer,
            )
        assert consumer.call_count == 1


@pytest.mark.parametrize("at_final_guard", [False, True])
async def test_catalogue_change_before_consumption_withholds_all(monkeypatch, at_final_guard):
    row = fact()
    request = composed(claim_for(row))
    monkeypatch.setattr(service, "load_accepted_state", AsyncMock(return_value=([row], [row])))
    monkeypatch.setattr(service, "authorize_patient_scope", AsyncMock())
    async with AsyncSession() as db:
        record = (
            await service.verify_patient_evidence_aware_draft(
                db,
                AUTHOR,
                PATIENT,
                request,
            )
        ).verification
        changed = (
            Mock(side_effect=[record.catalogue_fingerprint, None])
            if at_final_guard
            else Mock(return_value=None)
        )
        monkeypatch.setattr(service, "catalogue_fingerprint", changed)
        consumer = Mock()
        with pytest.raises(HTTPException) as error:
            await service.consume_patient_evidence_aware_draft(
                db,
                AUTHOR,
                PATIENT,
                request,
                record,
                consumer,
            )
        assert error.value.status_code == 503
        consumer.assert_not_called()


async def test_revalidation_rejects_constructed_models_and_untrusted_flags(monkeypatch):
    request = composed(claim_for(fact()))
    forged = request.model_copy(update={"bindings": ({"approved": True},)})
    monkeypatch.setattr(service, "load_accepted_state", AsyncMock())
    async with AsyncSession() as db:
        with pytest.raises(HTTPException) as error:
            await service.verify_patient_evidence_aware_draft(db, AUTHOR, PATIENT, forged)
    assert error.value.status_code == 422
    assert error.value.detail == "Invalid evidence-aware draft request"


async def test_async_consumer_is_rejected_before_receiving_records():
    row = fact()
    request = composed(claim_for(row))
    record = evaluate(request, [row])
    consumer = AsyncMock()
    async with AsyncSession() as db:
        with pytest.raises(TypeError, match="must be synchronous"):
            await service.consume_patient_evidence_aware_draft(
                db,
                AUTHOR,
                PATIENT,
                request,
                record,
                consumer,
            )
    consumer.assert_not_called()


async def test_database_isolation_and_current_consent(db, api):
    patient, profile = await add_patient(db)
    doctor, practitioner = await add_practitioner(db)
    other, _ = await add_patient(db)
    patient_id, doctor_id, practitioner_id = (
        profile.id,
        doctor.id,
        practitioner.id,
    )
    response = (await api.post(path(patient_id), headers=headers(patient), json=entry())).json()
    row = version_input(await db.get(ClinicalFactVersion, UUID(response["version_id"])))
    request = composed(claim_for(row))
    with pytest.raises(HTTPException) as error:
        await service.verify_patient_evidence_aware_draft(db, other.id, patient_id, request)
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        await service.verify_patient_evidence_aware_draft(db, doctor_id, patient_id, request)
    assert error.value.status_code == 403
    await db.rollback()
    await db.refresh(patient)
    assert (await grant(api, patient, practitioner_id)).status_code == 200
    record = (
        await service.verify_patient_evidence_aware_draft(db, doctor_id, patient_id, request)
    ).verification
    assert record.claims[0].text_verified
    await db.rollback()
    await db.refresh(patient)
    assert (await revoke(api, patient, practitioner_id)).status_code == 200
    consumer = Mock()
    with pytest.raises(HTTPException) as error:
        await service.consume_patient_evidence_aware_draft(
            db, doctor_id, patient_id, request, record, consumer
        )
    assert error.value.status_code == 403
    consumer.assert_not_called()
    await db.rollback()


async def test_database_read_only_confirmation_pending_rejected_and_correction(db, api):
    from tests.test_extraction import decide, extract

    patient, profile = await add_patient(db)
    patient_id, account_id = profile.id, patient.id
    auth = headers(patient)
    items = (await extract(api, patient_id, auth)).json()["confirmations"]
    assert (await decide(api, patient_id, auth, items[0]["confirmation_id"])).status_code == 200
    decision = await db.get(ConfirmationDecision, UUID(items[0]["confirmation_id"]))
    before_decision = (decision.status, decision.decided_by_id, decision.accepted_version_id)
    row = version_input(await db.get(ClinicalFactVersion, decision.accepted_version_id))
    pending, rejected = (
        candidate(patient_id, account_id),
        candidate(patient_id, account_id, status="REJECTED"),
    )
    db.add_all([pending, rejected])
    await db.commit()
    unavailable = (pending.id, rejected.id)
    rows_before = {
        r.id: version_input(r).model_dump_json()
        for r in await db.scalars(select(ClinicalFactVersion))
    }
    record = (
        await service.verify_patient_evidence_aware_draft(
            db, account_id, patient_id, composed(claim_for(row))
        )
    ).verification
    assert record.claims[0].grounding.outcome == "GROUNDED"
    for missing in unavailable:
        blocked = await service.verify_patient_evidence_aware_draft(
            db, account_id, patient_id, composed(claim_for(row, fact_version_ids=(missing,)))
        )
        assert not blocked.verification.claims[0].text_verified
        assert str(missing) not in blocked.model_dump_json()
    assert before_decision == (
        decision.status,
        decision.decided_by_id,
        decision.accepted_version_id,
    )
    assert not db.new and not db.dirty and not db.deleted
    assert rows_before == {
        r.id: version_input(r).model_dump_json()
        for r in await db.scalars(select(ClinicalFactVersion))
    }
    # Correct via the existing authorized API, never mutate a historical version.
    response = await api.post(
        f"{path(patient_id)}/{row.fact_id}/corrections",
        headers=auth,
        json={
            **entry("7.3"),
            "expected_version_id": str(row.version_id),
            "correction_reason": "synthetic correction",
        },
    )
    assert response.status_code == 201
    consumer = Mock()
    with pytest.raises(HTTPException) as error:
        await service.consume_patient_evidence_aware_draft(
            db, account_id, patient_id, composed(claim_for(row)), record, consumer
        )
    assert error.value.status_code == 409
    consumer.assert_not_called()
    await db.rollback()


@pytest.mark.parametrize("revoke_first", [True, False])
async def test_database_revocation_protected_consumption(db, race_environment, revoke_first):
    env = race_environment
    env["hold_revoke"] = revoke_first
    account_id = decode_access_token(env["doctor_auth"]["Authorization"].split()[1])
    row = version_input(
        await db.scalar(
            select(ClinicalFactVersion).where(ClinicalFactVersion.patient_id == env["patient_id"])
        )
    )
    request = composed(claim_for(row))
    async with (
        AsyncClient(transport=ASGITransport(app=env["app"]), base_url="http://test") as client,
        AsyncSession(env["engine"], expire_on_commit=False) as session,
    ):

        async def check():
            env["pids"]["clinical"] = await session.scalar(text("SELECT pg_backend_pid()"))
            return await service.verify_patient_evidence_aware_draft(
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
            record = (await check()).verification
            revoking = asyncio.create_task(revocation())
            try:
                await wait_until_blocked(db, env, "revoke")
                consumer = Mock()
                await service.consume_patient_evidence_aware_draft(
                    session, account_id, env["patient_id"], request, record, consumer
                )
                consumer.assert_called_once()
                assert not revoking.done()
            finally:
                await session.rollback()
            assert (await revoking).status_code == 200


async def test_database_catalogue_change_before_consumption(db, api, monkeypatch):
    patient, profile = await add_patient(db)
    patient_id, account_id = profile.id, patient.id
    response = (await api.post(path(patient_id), headers=headers(patient), json=entry())).json()
    row = version_input(await db.get(ClinicalFactVersion, UUID(response["version_id"])))
    request = composed(claim_for(row))
    record = (
        await service.verify_patient_evidence_aware_draft(db, account_id, patient_id, request)
    ).verification
    monkeypatch.setattr(service, "catalogue_fingerprint", lambda: None)
    consumer = Mock()
    with pytest.raises(HTTPException) as error:
        await service.consume_patient_evidence_aware_draft(
            db, account_id, patient_id, request, record, consumer
        )
    assert error.value.status_code == 503
    consumer.assert_not_called()
    await db.rollback()


async def test_database_correction_waits_for_consumption_and_stale_result_is_withheld(
    db,
    race_environment,
):
    env = race_environment
    patient_id = env["patient_id"]
    account_id = decode_access_token(env["doctor_auth"]["Authorization"].split()[1])
    row = version_input(
        await db.scalar(
            select(ClinicalFactVersion).where(
                ClinicalFactVersion.patient_id == patient_id,
            )
        )
    )
    request = composed(claim_for(row))
    async with (
        AsyncSession(env["engine"], expire_on_commit=False) as session,
        AsyncClient(transport=ASGITransport(app=env["app"]), base_url="http://test") as client,
    ):
        record = (
            await service.verify_patient_evidence_aware_draft(
                session,
                account_id,
                patient_id,
                request,
            )
        ).verification
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
            consumer = Mock()
            await service.consume_patient_evidence_aware_draft(
                session,
                account_id,
                patient_id,
                request,
                record,
                consumer,
            )
            consumer.assert_called_once()
            assert not correcting.done()
        finally:
            await session.rollback()
        assert (await correcting).status_code == 201
        consumer.reset_mock()
        with pytest.raises(HTTPException) as error:
            await service.consume_patient_evidence_aware_draft(
                session,
                account_id,
                patient_id,
                request,
                record,
                consumer,
            )
        assert error.value.status_code == 409
        consumer.assert_not_called()
        await session.rollback()
