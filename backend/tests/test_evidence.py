"""Synthetic approval/resolution/context only; no medical evidence or care rules."""

import asyncio
import json
import secrets
from dataclasses import FrozenInstanceError, replace
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
from app.schemas.evidence import RuleSelection, VerificationRequest
from app.services import evidence_applicability as service
from app.services import evidence_catalog as catalog_module
from app.services.evidence_catalog import (
    CONTEXT,
    canonical_hash,
    load_catalog,
    prepare_evidence,
    resolve_catalog,
)
from app.services.evidence_evaluator import EvidenceInputError, evaluate_evidence_context
from tests.test_auth import add_patient, add_practitioner, headers
from tests.test_clinical_facts import candidate, entry, path, wait_until_blocked
from tests.test_clinical_facts import race_environment as race_environment
from tests.test_consents import grant, revoke
from tests.test_consistency import AUTHOR, PATIENT, correction, fact


@pytest.fixture(autouse=True)
def signing_configuration(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", secrets.token_urlsafe(48))


def request_for(patient_id=PATIENT, ids=(), *, all_rules=False, **changes):
    catalog = load_catalog()
    rules = catalog.registry.rules if all_rules else catalog.registry.rules[:1]
    fields = dict(
        patient_id=patient_id,
        fact_version_ids=tuple(ids),
        rules=tuple(
            RuleSelection(rule_id=r.rule_id, rule_version=r.version, evidence=r.evidence)
            for r in rules
        ),
        approval_manifest_id=catalog.manifest.manifest_id,
        approval_manifest_version=catalog.manifest.version,
        intended_use="synthetic-context-testing",
    )
    fields.update(changes)
    return VerificationRequest.model_validate(fields)


def altered(catalog, **changes):
    result = replace(catalog, **changes)
    return replace(result, manifest_hash=canonical_hash(result.manifest.model_dump(mode="json")))


def test_approved_synthetic_resolution_and_context():
    a = fact()
    request = request_for(ids=[a.version_id])
    prepared = prepare_evidence(request)
    assert prepared.manifest_hash is not None
    assert prepared.rules[0].result.reason_code == "SYNTHETIC_EVIDENCE_RESOLVED"
    result = evaluate_evidence_context(request, [a], [a], prepared)
    assert result.status == "RESOLVED" and result.reason_code == "SYNTHETIC_CONTEXT_MATCHED"
    assert result.rules[0].checks_run[-1] == CONTEXT
    assert result.rules[0].matched_fact_version_ids == (a.version_id,)
    assert result.rules[0].evidence == request.rules[0].evidence
    assert result.snapshot.current_version_ids == (a.version_id,)
    assert "evaluated_at" not in result.model_dump()
    assert "content" not in result.rules[0].model_dump()


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("missing_source", "SOURCE_UNAVAILABLE"),
        ("source_version", "SOURCE_UNAVAILABLE"),
        ("edition", "SOURCE_EDITION_MISMATCH"),
        ("source_hash", "SOURCE_HASH_MISMATCH"),
        ("missing_unit", "UNIT_UNAVAILABLE"),
        ("unit_version", "UNIT_UNAVAILABLE"),
        ("unit_hash", "UNIT_HASH_MISMATCH"),
        ("locator", "INVALID_LOCATOR"),
        ("withdrawn", "APPROVAL_WITHDRAWN"),
        ("unapproved", "EVIDENCE_NOT_APPROVED"),
        ("wrong_rule_approval", "EVIDENCE_NOT_APPROVED"),
        ("ambiguous_approval", "EVIDENCE_NOT_APPROVED"),
    ],
)
def test_resolution_denials(case, reason):
    catalog = load_catalog()
    source, unit = catalog.bundle.sources[0], catalog.bundle.units[0]
    sources, units = catalog.bundle.sources, catalog.bundle.units
    approvals = catalog.manifest.approvals
    if case == "missing_source":
        sources = ()
    elif case == "source_version":
        sources = (source.model_copy(update={"version": "other"}),)
    elif case == "edition":
        sources = (source.model_copy(update={"edition": "other"}),)
    elif case == "source_hash":
        sources = (source.model_copy(update={"title": "tampered synthetic title"}),)
    elif case == "missing_unit":
        units = ()
    elif case == "unit_version":
        units = (unit.model_copy(update={"version": "other"}),)
    elif case == "unit_hash":
        units = (unit.model_copy(update={"content": "tampered synthetic fixture"}),)
    elif case == "locator":
        units = (unit.model_copy(update={"locator": "section:missing"}),)
    elif case == "withdrawn":
        approvals = (approvals[0].model_copy(update={"status": "withdrawn"}), *approvals[1:])
    elif case == "unapproved":
        approvals = approvals[1:]
    elif case == "wrong_rule_approval":
        approvals = (
            approvals[0].model_copy(update={"rule_content_hash": "0" * 64}),
            *approvals[1:],
        )
    elif case == "ambiguous_approval":
        approvals = (*approvals, approvals[0])
    simulated_trusted = altered(
        catalog,
        bundle=catalog.bundle.model_copy(update={"sources": sources, "units": units}),
        manifest=catalog.manifest.model_copy(update={"approvals": approvals}),
    )
    result = resolve_catalog(request_for(), simulated_trusted).rules[0].result
    assert result.status == "UNAVAILABLE" and result.reason_code == reason
    assert not result.evidence and not result.matched_fact_version_ids
    assert any(s.rule_id == CONTEXT for s in result.checks_not_applicable)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"approval_manifest_id": "unknown"}, "MANIFEST_VERSION_MISMATCH"),
        ({"approval_manifest_version": "other"}, "MANIFEST_VERSION_MISMATCH"),
        ({"intended_use": "clinical-care"}, "INTENDED_USE_NOT_APPROVED"),
    ],
)
def test_manifest_and_intended_use(changes, reason):
    request = request_for(**changes)
    assert prepare_evidence(request).rules[0].result.reason_code == reason


@pytest.mark.parametrize(
    "case",
    ["rule_version", "reference_hash", "reference_version", "reference_edition", "reference_unit"],
)
def test_request_cannot_substitute_bindings(case):
    request = request_for()
    selection = request.rules[0]
    ref = selection.evidence[0]
    if case == "rule_version":
        selection = selection.model_copy(update={"rule_version": "other"})
    else:
        field = {
            "reference_hash": "unit_content_hash",
            "reference_version": "source_version",
            "reference_edition": "source_edition",
            "reference_unit": "unit_id",
        }[case]
        ref = ref.model_copy(update={field: "0" * 64 if case == "reference_hash" else "other"})
        selection = selection.model_copy(update={"evidence": (ref,)})
    request = request.model_copy(update={"rules": (selection,)})
    assert prepare_evidence(request).rules[0].result.status == "UNAVAILABLE"


def test_rule_logic_and_content_integrity():
    catalog = load_catalog()
    rule = catalog.registry.rules[0]
    modified = rule.model_copy(update={"requirements": ()})
    changed = altered(catalog, registry=catalog.registry.model_copy(update={"rules": (modified,)}))
    assert (
        resolve_catalog(request_for(), changed).rules[0].result.reason_code == "RULE_HASH_MISMATCH"
    )
    values = rule.model_dump()
    values["logic_version"] = "execute-clinical-rule"
    with pytest.raises(ValidationError):
        type(rule).model_validate(values)


@pytest.mark.parametrize("filename", ["bundle.json", "rules.json", "approvals.json"])
def test_pinned_loader_rejects_file_tampering(tmp_path, monkeypatch, filename):
    for name in catalog_module.PINS:
        (tmp_path / name).write_bytes((catalog_module.ASSETS / name).read_bytes())
    path_value = tmp_path / filename
    data = json.loads(path_value.read_bytes())
    data["label"] = "REAL_CLINICAL_EVIDENCE"
    path_value.write_text(json.dumps(data))
    monkeypatch.setattr(catalog_module, "ASSETS", tmp_path)
    # request creation needs the original pinned catalogue; build from known contracts.
    selection = RuleSelection(
        rule_id="synthetic-patient-lab-context", rule_version="fixture-1", evidence=()
    )
    request = VerificationRequest(
        patient_id=PATIENT,
        fact_version_ids=(),
        rules=(selection,),
        approval_manifest_id="synthetic-evidence-approvals",
        approval_manifest_version="fixture-1",
        intended_use="synthetic-context-testing",
    )
    result = prepare_evidence(request)
    assert result.manifest_hash is None
    assert result.rules[0].result.status == "ERROR"
    assert result.rules[0].result.reason_code == "CATALOG_UNAVAILABLE_OR_INVALID"


def test_missing_assets_and_duplicate_json_fail_closed(tmp_path, monkeypatch):
    request = request_for()
    monkeypatch.setattr(catalog_module, "ASSETS", tmp_path)
    assert prepare_evidence(request).rules[0].result.status == "ERROR"
    for name in catalog_module.PINS:
        (tmp_path / name).write_text(
            '{"label":"SYNTHETIC_FIXTURE_ONLY","label":"SYNTHETIC_FIXTURE_ONLY"}'
        )
    assert prepare_evidence(request).rules[0].result.status == "ERROR"


def test_approval_flags_paths_and_free_text_qualifiers_are_not_request_fields():
    values = request_for().model_dump()
    for field in ["approved", "manifest", "path", "qualifiers", "evidence_content"]:
        with pytest.raises(ValidationError):
            VerificationRequest.model_validate({**values, field: True})
    with pytest.raises(ValidationError):
        VerificationRequest.model_validate(
            {**values, "rules": (*values["rules"], *values["rules"])}
        )


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        (
            {
                "fact": {
                    "kind": "symptom",
                    "code": "fatigue",
                    "description": "hba1c % patient_reported",
                }
            },
            "FACT_KIND_MISMATCH",
        ),
        (
            {"fact": {"kind": "laboratory", "code": "glucose", "value": "110", "unit": "mg/dL"}},
            "FACT_CODE_MISMATCH",
        ),
        (
            {"fact": {"kind": "laboratory", "code": "hba1c", "value": "50", "unit": "mmol/mol"}},
            "UNIT_MISMATCH_NO_CONVERSION",
        ),
        (
            {"attribution": "practitioner_managed", "author_role": "practitioner"},
            "STRUCTURED_QUALIFIER_MISMATCH",
        ),
    ],
)
def test_structured_context_only(changes, reason):
    a = fact(**changes)
    request = request_for(ids=[a.version_id])
    result = evaluate_evidence_context(request, [a], [a], prepare_evidence(request))
    assert result.status == "NOT_APPLICABLE"
    assert result.rules[0].reason_code == reason
    assert not result.rules[0].matched_fact_version_ids


def test_missing_context_never_uses_unselected_facts():
    a = fact()
    request = request_for()
    result = evaluate_evidence_context(request, [a], [a], prepare_evidence(request))
    assert result.status == "INSUFFICIENT_CONTEXT"
    assert result.rules[0].reason_code == "REQUIRED_FACT_MISSING"


@pytest.mark.parametrize("case", ["pending", "rejected", "superseded", "missing"])
def test_only_current_accepted_context_selected(case):
    a = fact()
    request = request_for(ids=[a.version_id])
    current, history = [a], [a]
    if case in {"pending", "rejected"}:
        current = history = [fact(status=case.upper())]
    elif case == "superseded":
        c = correction(a)
        current, history = [c], [a, c]
    else:
        request = request_for(ids=[uuid4()])
    with pytest.raises(EvidenceInputError) as error:
        evaluate_evidence_context(request, current, history, prepare_evidence(request))
    assert str(error.value) == "Requested accepted context unavailable"


@pytest.mark.parametrize("location", ["current", "history"])
def test_mixed_patient_isolation(location):
    request = request_for()
    foreign = fact(patient_id=uuid4())
    with pytest.raises(ValueError) as error:
        evaluate_evidence_context(
            request,
            [foreign] if location == "current" else [],
            [foreign] if location == "history" else [],
            prepare_evidence(request),
        )
    assert str(error.value) == "Patient scope mismatch"


def test_structural_failure_blocks_context_and_no_numeric_clinical_rule():
    a = fact()
    bad = correction(a, correction_reason=" ")
    request = request_for(ids=[bad.version_id])
    result = evaluate_evidence_context(request, [bad], [a, bad], prepare_evidence(request))
    assert result.status == "ERROR" and result.rules[0].reason_code == "ACCEPTED_STATE_INVALID"
    assert CONTEXT not in result.rules[0].checks_run
    # Values have no clinical role in this fixture.
    extreme = fact(fact={"kind": "laboratory", "code": "hba1c", "value": "999", "unit": "%"})
    request = request_for(ids=[extreme.version_id])
    assert (
        evaluate_evidence_context(request, [extreme], [extreme], prepare_evidence(request)).status
        == "RESOLVED"
    )


def test_determinism_and_immutable_inputs():
    a, b = fact(), fact(20)
    request = request_for(ids=[a.version_id, b.version_id], all_rules=True)
    catalog = load_catalog()
    before = (
        request.model_dump_json(),
        a.model_dump_json(),
        catalog.bundle.model_dump_json(),
        catalog.manifest.model_dump_json(),
    )
    prepared = prepare_evidence(request)
    result = evaluate_evidence_context(request, [a, b], [a, b], prepared)
    reversed_request = request.model_copy(
        update={
            "rules": tuple(reversed(request.rules)),
            "fact_version_ids": tuple(reversed(request.fact_version_ids)),
        }
    )
    assert (
        result.model_dump_json()
        == evaluate_evidence_context(
            reversed_request, [b, a], [b, a], prepare_evidence(reversed_request)
        ).model_dump_json()
    )
    assert before == (
        request.model_dump_json(),
        a.model_dump_json(),
        catalog.bundle.model_dump_json(),
        catalog.manifest.model_dump_json(),
    )
    with pytest.raises(ValidationError):
        catalog.bundle.sources[0].version = "other"
    with pytest.raises(FrozenInstanceError):
        catalog.manifest_hash = "0" * 64


def test_no_rule_selection_is_explicit_noop():
    request = request_for(rules=())
    result = evaluate_evidence_context(request, [], [], prepare_evidence(request))
    assert result.status == "NOT_APPLICABLE" and result.reason_code == "NO_RULES_REQUESTED"


async def test_service_order_no_mutation_and_catalogue_recheck(monkeypatch):
    a = fact()
    request = request_for(ids=[a.version_id])
    events = []
    original = prepare_evidence

    def prepare(value):
        events.append("evidence")
        return original(value)

    async def load(*args):
        events.append("patient-lock")
        return [a], [a]

    async def authorize(*args):
        events.append("authorize")

    monkeypatch.setattr(service, "prepare_evidence", prepare)
    monkeypatch.setattr(service, "load_accepted_state", load)
    monkeypatch.setattr(service, "authorize_patient_scope", authorize)
    async with AsyncSession() as db:
        db.commit, db.flush = AsyncMock(), AsyncMock()
        run = await service.evaluate_patient_evidence(db, AUTHOR, request)
        assert events == ["evidence", "patient-lock", "evidence", "authorize"]
        assert run.evaluation.status == "RESOLVED" and run.evaluated_at.tzinfo is not None
        db.commit.assert_not_awaited()
        db.flush.assert_not_awaited()
        assert not db.new and not db.dirty and not db.deleted


@pytest.mark.parametrize("case", ["consent", "withdrawal", "unavailable_context"])
async def test_service_withholds_results_on_boundary_failure(monkeypatch, case):
    a = fact()
    request = request_for(ids=[a.version_id] if case != "unavailable_context" else [uuid4()])
    monkeypatch.setattr(service, "load_accepted_state", AsyncMock(return_value=([a], [a])))
    monkeypatch.setattr(
        service,
        "authorize_patient_scope",
        AsyncMock(
            side_effect=HTTPException(403, "Patient scope denied") if case == "consent" else None
        ),
    )
    if case == "withdrawal":
        catalog = load_catalog()
        withdrawn = altered(
            catalog,
            manifest=catalog.manifest.model_copy(
                update={
                    "approvals": tuple(
                        a.model_copy(update={"status": "withdrawn"})
                        for a in catalog.manifest.approvals
                    )
                }
            ),
        )
        monkeypatch.setattr(
            service,
            "prepare_evidence",
            Mock(side_effect=[prepare_evidence(request), resolve_catalog(request, withdrawn)]),
        )
    async with AsyncSession() as db:
        with pytest.raises(HTTPException) as error:
            await service.evaluate_patient_evidence(db, AUTHOR, request)
    assert (
        error.value.status_code
        == {"consent": 403, "withdrawal": 503, "unavailable_context": 409}[case]
    )


async def test_database_only_confirmed_context_and_read_only(db, api):
    patient, profile = await add_patient(db)
    account_id, patient_id = patient.id, profile.id
    accepted = (await api.post(path(patient_id), headers=headers(patient), json=entry())).json()
    pending, rejected = (
        candidate(patient_id, account_id),
        candidate(patient_id, account_id, status="REJECTED"),
    )
    db.add_all([pending, rejected])
    await db.commit()
    before = {
        r.id: (dict(r.payload), r.status) for r in await db.scalars(select(ClinicalFactVersion))
    }
    decisions = list(await db.scalars(select(ConfirmationDecision.proposal_id)))
    request = request_for(patient_id, [UUID(accepted["version_id"])])
    run = await service.evaluate_patient_evidence(db, account_id, request)
    assert (
        run.evaluation.status == "RESOLVED"
        and len(run.evaluation.snapshot.history_version_ids) == 1
    )
    assert db.in_transaction() and not db.new and not db.dirty and not db.deleted
    assert before == {
        r.id: (dict(r.payload), r.status) for r in await db.scalars(select(ClinicalFactVersion))
    }
    assert decisions == list(await db.scalars(select(ConfirmationDecision.proposal_id)))
    for unavailable in (pending.id, rejected.id):
        with pytest.raises(HTTPException) as error:
            await service.evaluate_patient_evidence(
                db, account_id, request_for(patient_id, [unavailable])
            )
        assert error.value.status_code == 409
    await db.rollback()


async def test_database_consent_and_revocation(db, api):
    patient, profile = await add_patient(db)
    doctor, practitioner = await add_practitioner(db)
    account_id, patient_id, practitioner_id = doctor.id, profile.id, practitioner.id
    await db.commit()
    request = request_for(patient_id)
    with pytest.raises(HTTPException) as error:
        await service.evaluate_patient_evidence(db, account_id, request)
    assert error.value.status_code == 403
    await db.rollback()
    await db.refresh(patient)
    assert (await grant(api, patient, practitioner_id)).status_code == 200
    assert (
        await service.evaluate_patient_evidence(db, account_id, request)
    ).evaluation.status == "INSUFFICIENT_CONTEXT"
    await db.rollback()
    await db.refresh(patient)
    assert (await revoke(api, patient, practitioner_id)).status_code == 200
    with pytest.raises(HTTPException) as error:
        await service.evaluate_patient_evidence(db, account_id, request)
    assert error.value.status_code == 403
    await db.rollback()


async def test_database_patient_and_foreign_version_isolation(db, api):
    patient, profile = await add_patient(db)
    other, other_profile = await add_patient(db)
    account_id, patient_id, other_id = patient.id, profile.id, other_profile.id
    foreign = (await api.post(path(other_id), headers=headers(other), json=entry())).json()
    with pytest.raises(HTTPException) as error:
        await service.evaluate_patient_evidence(db, account_id, request_for(other_id))
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        await service.evaluate_patient_evidence(
            db, account_id, request_for(patient_id, [UUID(foreign["version_id"])])
        )
    assert error.value.status_code == 409 and error.value.detail == "Evidence context unavailable"
    await db.rollback()


@pytest.mark.parametrize("revoke_first", [True, False])
async def test_database_revocation_gate(db, race_environment, revoke_first):
    env = race_environment
    env["hold_revoke"] = revoke_first
    account_id = decode_access_token(env["doctor_auth"]["Authorization"].split()[1])
    request = request_for(env["patient_id"])
    async with (
        AsyncClient(transport=ASGITransport(app=env["app"]), base_url="http://test") as client,
        AsyncSession(env["engine"], expire_on_commit=False) as session,
    ):

        async def check():
            env["pids"]["clinical"] = await session.scalar(text("SELECT pg_backend_pid()"))
            return await service.evaluate_patient_evidence(session, account_id, request)

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
            await check()
            revoking = asyncio.create_task(revocation())
            try:
                await wait_until_blocked(db, env, "revoke")
                assert not revoking.done()
            finally:
                await session.rollback()
            assert (await revoking).status_code == 200


def test_unapproved_evidence_cannot_resolve_patient_context():
    a = fact()
    request = request_for(ids=[a.version_id], intended_use="clinical-care")
    result = evaluate_evidence_context(request, [a], [a], prepare_evidence(request))
    assert result.status == "UNAVAILABLE"
    assert not result.rules[0].matched_fact_version_ids
    assert CONTEXT not in result.rules[0].checks_run


def test_unsupported_qualifiers_cannot_read_prose_or_arbitrary_fields():
    from app.schemas.evidence import StructuredQualifier

    for values in (
        {"field": "description", "equals": "hba1c"},
        {"field": "attribution", "equals": "clinical-truth"},
    ):
        with pytest.raises(ValidationError):
            StructuredQualifier.model_validate(values)


async def test_service_unauthorized_even_when_evidence_is_unavailable(monkeypatch):
    request = request_for(intended_use="clinical-care")
    monkeypatch.setattr(
        service,
        "load_accepted_state",
        AsyncMock(side_effect=HTTPException(403, "Patient scope denied")),
    )
    async with AsyncSession() as db:
        with pytest.raises(HTTPException) as error:
            await service.evaluate_patient_evidence(db, AUTHOR, request)
        assert error.value.status_code == 403


async def test_database_confirmed_extraction_decision_unchanged(db, api):
    from tests.test_extraction import decide, extract

    patient, profile = await add_patient(db)
    account_id, patient_id = patient.id, profile.id
    auth = headers(patient)
    items = (await extract(api, patient_id, auth)).json()["confirmations"]
    confirmed = await decide(api, patient_id, auth, items[0]["confirmation_id"])
    assert confirmed.status_code == 200
    decision = await db.get(ConfirmationDecision, UUID(items[0]["confirmation_id"]))
    before = (decision.status, decision.decided_by_id, decision.accepted_version_id)
    request = request_for(patient_id, [decision.accepted_version_id])
    run = await service.evaluate_patient_evidence(db, account_id, request)
    assert run.evaluation.status == "RESOLVED"
    assert before == (decision.status, decision.decided_by_id, decision.accepted_version_id)
    assert decision.decided_by_id == account_id
    assert run.evaluation.snapshot.current_version_ids == (decision.accepted_version_id,)
    await db.rollback()
