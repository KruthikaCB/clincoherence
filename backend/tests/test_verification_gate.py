"""Technical record eligibility, never clinical correctness or HTTP permission."""

import asyncio
import secrets
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
from app.schemas.consistency import Finding
from app.schemas.verification_gate import GateInput
from app.services import evidence_aware_draft as evidence_service
from app.services import verification_gate as service
from app.services.consistency import version_input
from app.services.gate_evaluator import GateInputError, evaluate_gate
from tests.test_auth import add_patient, add_practitioner, headers
from tests.test_clinical_facts import candidate, entry, path, wait_until_blocked
from tests.test_clinical_facts import race_environment as race_environment
from tests.test_consents import grant, revoke
from tests.test_consistency import AUTHOR, PATIENT, correction, fact
from tests.test_draft_verification import claim_for
from tests.test_evidence_aware_draft import composed, evaluate


@pytest.fixture(autouse=True)
def signing_configuration(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", secrets.token_urlsafe(48))


def gated(request, rows, history=None, record=None):
    return evaluate_gate(
        GateInput(
            request=request,
            verification=evaluate(request, rows, history) if record is None else record,
        )
    )


@pytest.mark.parametrize("bound", [False, True])
def test_pass_is_internal_record_eligibility_only(bound):
    row = fact()
    gate = gated(composed(claim_for(row), bound=bound), [row])
    assert gate.decision == "PASS" and len(gate.rendered_records) == 1
    assert gate.claims[0].reason_codes == (
        ("SYNTHETIC_REFERENCE_CHECKS_COMPLETE" if bound else "ACCEPTED_RECORD_RESTATEMENT"),
    )
    assert not gate.http_release_authorized and not gate.unrestricted_text_verified
    assert gate.verification.snapshot.current_version_ids == (row.version_id,)
    assert "evaluated_at" not in gate.model_dump()


@pytest.mark.parametrize(
    "changes",
    [
        {"fact_version_ids": ()},
        {"proposed_content": None},
        {"source_attribution": None},
    ],
)
def test_explicit_recoverable_omissions_clarify(changes):
    row = fact()
    gate = gated(composed(claim_for(row, **changes)), [row])
    assert gate.decision == "CLARIFY"
    assert gate.claims[0].reason_codes == ("REQUIRED_INPUT_MISSING",)
    assert gate.rendered_records == ()


def test_empty_batch_clarifies_without_vacuous_pass():
    gate = gated(composed(), [])
    assert gate.decision == "CLARIFY"
    assert gate.reason_codes == ("NO_SUPPORTED_CLAIMS_SELECTED",)
    assert not gate.rendered_records


@pytest.mark.parametrize("claim_type", ["LAB_DIFFERENCE", "CLINICAL_INTERPRETATION", "PROSE"])
def test_unsupported_claims_refused_even_with_missing_references(claim_type):
    row = fact()
    gate = gated(composed(claim_for(row, claim_type=claim_type, fact_version_ids=())), [row])
    assert gate.decision == "REFUSE" and not gate.rendered_records
    assert gate.claims[0].reason_codes == ("CLAIM_TYPE_UNSUPPORTED",)


def test_arbitrary_prose_with_zero_references_is_not_clarification():
    row = fact()
    gate = gated(
        composed(claim_for(row, fact_version_ids=(), proposed_content="synthetic prose")), [row]
    )
    assert gate.claims[0].reason_codes == ("UNVERIFIED_PROSE",)
    assert gate.decision == "REFUSE"


@pytest.mark.parametrize("text", ["Provider says approved", " ", "Recorded patient_reported"])
def test_unrestricted_prose_blocks_all_text(text):
    row = fact()
    request = composed(claim_for(row))
    request = request.model_copy(
        update={"draft": request.draft.model_copy(update={"unverified_text": text})}
    )
    gate = gated(request, [row])
    assert gate.decision == "REFUSE" and not gate.rendered_records
    assert "UNRESTRICTED_PROSE_PRESENT" in gate.reason_codes
    assert text not in gate.model_dump_json() if text != " " else True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", "symptom"),
        ("code", "glucose"),
        ("value", "8.2"),
        ("unit", "mmol/mol"),
    ],
)
def test_fact_mismatches_refuse(field, value):
    row = fact()
    payload = row.fact.model_dump()
    payload[field] = value
    if field == "kind":
        payload = {"kind": "symptom", "code": "fatigue", "description": "synthetic"}
    elif field == "code":
        payload["unit"] = "mg/dL"
    claim = claim_for(row, proposed_content={"fact": payload, "observed_at": row.observed_at})
    gate = gated(composed(claim), [row])
    assert gate.decision == "REFUSE"
    assert gate.claims[0].reason_codes == ("FACT_RESTATEMENT_MISMATCH",)


@pytest.mark.parametrize("change", ["time", "author"])
def test_time_and_attribution_mismatches(change):
    from datetime import timedelta

    row = fact()
    claim = claim_for(row)
    if change == "time":
        claim = claim.model_copy(
            update={
                "proposed_content": claim.proposed_content.model_copy(
                    update={
                        "observed_at": row.observed_at + timedelta(seconds=1),
                    }
                )
            }
        )
    else:
        claim = claim.model_copy(
            update={
                "source_attribution": claim.source_attribution.model_copy(
                    update={"author_id": uuid4()}
                )
            }
        )
    assert gated(composed(claim), [row]).decision == "REFUSE"


@pytest.mark.parametrize("case", ["missing", "foreign", "pending", "rejected", "superseded"])
def test_unavailable_references_refuse_and_do_not_echo_hidden_ids(case):
    a, b = fact(), fact(20)
    current, history = [a], [a]
    if case == "foreign":
        b = b.model_copy(update={"patient_id": uuid4()})
    if case in {"pending", "rejected"}:
        b = b.model_copy(update={"status": case.upper()})
    if case == "superseded":
        successor = correction(b, 21)
        current, history = [a, successor], [a, b, successor]
    gate = gated(composed(claim_for(b, patient_id=PATIENT)), current, history)
    assert gate.decision == "REFUSE" and not gate.rendered_records
    assert gate.claims[0].reason_codes == ("ACCEPTED_REFERENCE_UNAVAILABLE",)
    if case != "superseded":
        assert str(b.version_id) not in gate.model_dump_json()


def test_invalid_provenance_and_catalogue_integrity_refuse_all():
    row = fact(entry_method="LLM_EXTRACTED", confirmed_by_id=None, confirmed_at=None)
    assert gated(composed(claim_for(row)), [row]).reason_codes == ("ACCEPTED_STATE_INVALID",)
    row = fact()
    request = composed(claim_for(row))
    record = evaluate(request, [row]).model_copy(update={"catalogue_fingerprint": None})
    gate = gated(request, [row], record=record)
    assert gate.reason_codes == ("CATALOGUE_INTEGRITY_INVALID",)
    assert not gate.rendered_records


@pytest.mark.parametrize(
    "case", ["withdrawn", "unapproved", "unavailable", "mismatch", "not_applicable", "insufficient"]
)
def test_evidence_failures_are_hard_refusals(case):
    row = fact()
    request = composed(claim_for(row))
    record = evaluate(request, [row])
    result = record.claims[0]
    evidence = result.evidence
    if case in {"not_applicable", "insufficient"}:
        context = evidence.applicability.model_copy(
            update={
                "status": "NOT_APPLICABLE" if case == "not_applicable" else "INSUFFICIENT_CONTEXT",
                "reason_code": "STRUCTURED_QUALIFIER_MISMATCH"
                if case == "not_applicable"
                else "REQUIRED_FACT_MISSING",
                "matched_fact_version_ids": (),
            }
        )
        evidence = evidence.model_copy(update={"applicability": context})
    else:
        reason = {
            "withdrawn": "APPROVAL_WITHDRAWN",
            "unapproved": "EVIDENCE_NOT_APPROVED",
            "unavailable": "SOURCE_UNAVAILABLE",
            "mismatch": "SOURCE_HASH_MISMATCH",
        }[case]
        evidence = evidence.model_copy(
            update={
                "integrity": evidence.integrity.model_copy(
                    update={
                        "status": "UNAVAILABLE",
                        "reason_code": reason,
                        "evidence": (),
                    }
                ),
                "applicability": None,
            }
        )
    result = result.model_copy(
        update={
            "evidence": evidence,
            "reason_code": "EVIDENCE_WITHHELD",
            "rendered_text": None,
            "text_verified": False,
        }
    )
    gate = gated(request, [row], record=record.model_copy(update={"claims": (result,)}))
    assert gate.decision == "REFUSE" and not gate.rendered_records


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "duplicate",
        "extra",
        "unknown_status",
        "unknown_reason",
        "wrong_binding",
        "snapshot",
        "policy",
        "unverified_text",
    ],
)
def test_unknown_or_inconsistent_records_fail_closed(case):
    row = fact()
    request = composed(claim_for(row))
    record = evaluate(request, [row])
    result = record.claims[0]
    if case == "missing":
        record = record.model_copy(update={"claims": ()})
    elif case == "duplicate":
        record = record.model_copy(update={"claims": (result, result)})
    elif case == "extra":
        record = record.model_copy(
            update={"claims": (result, result.model_copy(update={"claim_id": uuid4()}))}
        )
    elif case.startswith("unknown"):
        ground = result.grounding.model_copy(
            update={
                "outcome" if case == "unknown_status" else "reason_code": "FUTURE_UNKNOWN",
            }
        )
        record = record.model_copy(
            update={"claims": (result.model_copy(update={"grounding": ground}),)}
        )
    elif case == "wrong_binding":
        record = record.model_copy(
            update={"claims": (result.model_copy(update={"evidence": None}),)}
        )
    elif case == "snapshot":
        record = record.model_copy(
            update={"snapshot": record.snapshot.model_copy(update={"current_version_ids": ()})}
        )
    elif case == "policy":
        record = record.model_copy(update={"policy_version": "future-unreviewed"})
    else:
        record = record.model_copy(
            update={
                "claims": (
                    result.model_copy(update={"text_verified": False, "rendered_text": None}),
                )
            }
        )
    gate = gated(request, [row], record=record)
    assert gate.decision == "REFUSE" and gate.rendered_records == ()


def test_cannot_omit_unsupported_original_claim_to_obtain_pass():
    row = fact()
    good = claim_for(row)
    bad = claim_for(row, claim_id=UUID(int=101), claim_type="CLINICAL_INTERPRETATION")
    request = composed(good, bad)
    record = evaluate(request, [row])
    record = record.model_copy(update={"claims": record.claims[:1]})
    assert gated(request, [row], record=record).reason_codes == ("CLAIM_COVERAGE_INVALID",)


@pytest.mark.parametrize("second", ["clarify", "refuse"])
def test_mixed_batches_never_partially_release(second):
    row = fact()
    ca = claim_for(row)
    cb = claim_for(
        row,
        claim_id=UUID(int=101),
        **({"proposed_content": None} if second == "clarify" else {"claim_type": "LAB_DIFFERENCE"}),
    )
    gate = gated(composed(ca, cb), [row])
    assert gate.claims[0].decision == "PASS"
    assert gate.decision == ("CLARIFY" if second == "clarify" else "REFUSE")
    assert not gate.rendered_records


def test_refuse_dominates_clarification_and_bound_failure_dominates_missing_input():
    row = fact()
    ca = claim_for(row, proposed_content=None)
    cb = claim_for(row, claim_id=UUID(int=101), claim_type="LAB_DIFFERENCE")
    assert gated(composed(ca, cb), [row]).decision == "REFUSE"
    request = composed(ca)
    binding = request.bindings[0].model_copy(update={"intended_use": "clinical-care"})
    request = request.model_copy(update={"bindings": (binding,)})
    assert gated(request, [row]).decision == "REFUSE"


def test_multiple_references_is_malformed_not_missing():
    row, b = fact(), fact(20)
    gate = gated(
        composed(claim_for(row, fact_version_ids=(row.version_id, b.version_id))), [row, b]
    )
    assert gate.decision == "REFUSE"


@pytest.mark.parametrize("category", ["POSSIBLE_DUPLICATE", "NOT_COMPARABLE"])
def test_nonclinical_findings_are_not_warnings_or_contradictions(category):
    row = fact()
    request = composed(claim_for(row))
    record = evaluate(request, [row]).model_copy(
        update={
            "consistency_findings": (
                Finding(
                    rule_id="comparison",
                    category=category,
                    reason_code="EVENT_IDENTITY_NOT_ESTABLISHED",
                    fact_version_ids=(row.version_id,),
                ),
            )
        }
    )
    gate = gated(request, [row], record=record)
    assert gate.decision == "PASS"
    assert gate.verification.consistency_findings == record.consistency_findings
    with pytest.raises(ValidationError):
        type(gate).model_validate({**gate.model_dump(), "decision": "WARN"})


def test_deterministic_order_frozen_inputs_and_snapshot_binding():
    a, b = fact(), fact(20)
    ca, cb = claim_for(a), claim_for(b, claim_id=UUID(int=101))
    request = composed(ca, cb)
    before = request.model_dump_json()
    first = gated(request, [a, b])
    assert first == gated(composed(cb, ca), [b, a])
    assert before == request.model_dump_json()
    with pytest.raises(ValidationError):
        first.claims[0].decision = "WARN"


def test_mixed_patient_record_aborts_without_gate():
    row = fact()
    request = composed(claim_for(row))
    record = evaluate(request, [row]).model_copy(update={"patient_id": uuid4()})
    with pytest.raises(GateInputError, match="scope unavailable"):
        gated(request, [row], record=record)


async def test_adapter_recomputes_inside_4e_guard_and_exposes_only_pass(monkeypatch):
    row = fact()
    request = composed(claim_for(row))
    record = evaluate(request, [row])
    from datetime import UTC, datetime

    from app.schemas.evidence_aware_draft import EvidenceAwareRun

    verifier = AsyncMock(
        return_value=EvidenceAwareRun(evaluated_at=datetime.now(UTC), verification=record)
    )
    monkeypatch.setattr(service, "verify_patient_evidence_aware_draft", verifier)

    async def guard(db, account, patient, original, expected, callback):
        assert original == request and expected == record
        return callback(record)

    guarded = AsyncMock(side_effect=guard)
    monkeypatch.setattr(service, "consume_patient_evidence_aware_draft", guarded)
    async with AsyncSession() as db:
        run = await service.evaluate_patient_gate(db, AUTHOR, PATIENT, request)
        consumer = Mock(return_value="consumed")
        assert (
            await service.consume_patient_gate(db, AUTHOR, PATIENT, request, run.gate, consumer)
            == "consumed"
        )
        consumer.assert_called_once_with(run.gate)
        guarded.assert_awaited_once()
        with pytest.raises(HTTPException) as error:
            await service.consume_patient_gate(
                db,
                AUTHOR,
                PATIENT,
                request,
                run.gate.model_copy(update={"request_digest": "0" * 64}),
                consumer,
            )
        assert error.value.status_code == 409 and consumer.call_count == 1


@pytest.mark.parametrize("missing", [False, True])
async def test_adapter_never_consumes_withheld_batches(monkeypatch, missing):
    from datetime import UTC, datetime

    from app.schemas.evidence_aware_draft import EvidenceAwareRun

    row = fact()
    request = composed(claim_for(row, proposed_content=None if missing else "synthetic prose"))
    record = evaluate(request, [row])
    monkeypatch.setattr(
        service,
        "verify_patient_evidence_aware_draft",
        AsyncMock(
            return_value=EvidenceAwareRun(evaluated_at=datetime.now(UTC), verification=record)
        ),
    )

    async def guard(db, account, patient, original, expected, callback):
        return callback(record)

    monkeypatch.setattr(
        service, "consume_patient_evidence_aware_draft", AsyncMock(side_effect=guard)
    )
    async with AsyncSession() as db:
        run = await service.evaluate_patient_gate(db, AUTHOR, PATIENT, request)
        assert run.gate.decision == ("CLARIFY" if missing else "REFUSE")
        assert not run.gate.rendered_records
        consumer = Mock()
        with pytest.raises(HTTPException) as error:
            await service.consume_patient_gate(db, AUTHOR, PATIENT, request, run.gate, consumer)
        assert error.value.status_code == 409 and error.value.detail == "Gate withheld batch"
        consumer.assert_not_called()


async def test_mutable_envelope_cannot_drop_prose_during_verification(monkeypatch):
    from datetime import UTC, datetime

    from app.schemas.evidence_aware_draft import EvidenceAwareRun

    row = fact()
    request = composed(claim_for(row))
    draft = request.model_dump()
    draft["draft"]["unverified_text"] = "synthetic unverified provider prose"

    async def verifying(db, account, patient, frozen):
        draft["draft"]["unverified_text"] = None
        return EvidenceAwareRun(
            evaluated_at=datetime.now(UTC), verification=evaluate(frozen, [row])
        )

    monkeypatch.setattr(
        service, "verify_patient_evidence_aware_draft", AsyncMock(side_effect=verifying)
    )
    async with AsyncSession() as db:
        result = await service.evaluate_patient_gate(db, AUTHOR, PATIENT, draft)
    assert result.gate.decision == "REFUSE"
    assert "UNRESTRICTED_PROSE_PRESENT" in result.gate.reason_codes
    assert not result.gate.rendered_records


async def test_malformed_requests_preserve_authorization_failure(monkeypatch):
    verifier = AsyncMock(side_effect=HTTPException(403, "Patient scope denied"))
    monkeypatch.setattr(service, "verify_patient_evidence_aware_draft", verifier)
    async with AsyncSession() as db:
        with pytest.raises(HTTPException) as error:
            await service.evaluate_patient_gate(db, AUTHOR, PATIENT, {"decision": "PASS"})
    assert error.value.status_code == 403 and error.value.detail == "Patient scope denied"
    verifier.assert_awaited_once()


def test_invalid_original_request_is_not_clarification():
    request = composed(claim_for(fact()))
    malformed = request.model_copy(
        update={
            "draft": request.draft.model_copy(
                update={
                    "claims": request.draft.claims * 2,
                }
            )
        }
    )
    record = evaluate(request, [fact()])
    with pytest.raises(GateInputError, match="Invalid original gate request"):
        evaluate_gate(GateInput(request=malformed, verification=record))


@pytest.mark.parametrize("field", ["decision", "recoverable", "approved", "text_verified"])
def test_original_request_rejects_provider_policy_flags(field):
    from app.schemas.evidence_aware_draft import EvidenceAwareDraftRequest

    data = composed(claim_for(fact())).model_dump()
    data[field] = True
    with pytest.raises(ValidationError):
        EvidenceAwareDraftRequest.model_validate(data)


@pytest.mark.parametrize("field", ["status", "reason_code"])
def test_unknown_evidence_status_or_reason_is_refused(field):
    row = fact()
    request = composed(claim_for(row))
    record = evaluate(request, [row])
    result = record.claims[0]
    evidence = result.evidence.model_copy(
        update={
            "integrity": result.evidence.integrity.model_copy(
                update={
                    field: "FUTURE_UNKNOWN",
                }
            )
        }
    )
    record = record.model_copy(
        update={"claims": (result.model_copy(update={"evidence": evidence}),)}
    )
    gate = gated(request, [row], record=record)
    assert gate.decision == "REFUSE" and not gate.rendered_records
    assert "VERIFICATION_CONTRACT_INVALID" in gate.reason_codes


@pytest.mark.parametrize("field", ["grounding", "integrity", "context", "matched"])
def test_duplicated_verification_references_never_pass(field):
    row = fact()
    request = composed(claim_for(row))
    record = evaluate(request, [row])
    result = record.claims[0]
    evidence = result.evidence
    if field == "grounding":
        result = result.model_copy(
            update={
                "grounding": result.grounding.model_copy(
                    update={
                        "fact_version_ids": result.grounding.fact_version_ids * 2,
                    }
                )
            }
        )
    elif field == "integrity":
        result = result.model_copy(
            update={
                "evidence": evidence.model_copy(
                    update={
                        "integrity": evidence.integrity.model_copy(
                            update={"evidence": evidence.integrity.evidence * 2}
                        ),
                    }
                )
            }
        )
    else:
        context = evidence.applicability
        key = "evidence" if field == "context" else "matched_fact_version_ids"
        result = result.model_copy(
            update={
                "evidence": evidence.model_copy(
                    update={
                        "applicability": context.model_copy(
                            update={key: getattr(context, key) * 2}
                        ),
                    }
                )
            }
        )
    gate = gated(request, [row], record=record.model_copy(update={"claims": (result,)}))
    assert gate.decision == "REFUSE" and not gate.rendered_records


async def test_async_consumer_is_rejected_without_evaluation(monkeypatch):
    row = fact()
    request = composed(claim_for(row))
    gate = gated(request, [row])
    verifier = AsyncMock()
    monkeypatch.setattr(service, "verify_patient_evidence_aware_draft", verifier)
    consumer = AsyncMock()
    async with AsyncSession() as db:
        with pytest.raises(TypeError, match="must be synchronous"):
            await service.consume_patient_gate(db, AUTHOR, PATIENT, request, gate, consumer)
    verifier.assert_not_called()
    consumer.assert_not_called()


async def test_database_authorization_and_consent(db, api):
    patient, profile = await add_patient(db)
    doctor, practitioner = await add_practitioner(db)
    other, _ = await add_patient(db)
    patient_id, doctor_id, practitioner_id, other_id = (
        profile.id,
        doctor.id,
        practitioner.id,
        other.id,
    )
    response = (await api.post(path(patient_id), headers=headers(patient), json=entry())).json()
    row = version_input(await db.get(ClinicalFactVersion, UUID(response["version_id"])))
    request = composed(claim_for(row))
    for denied in (doctor_id, other_id):
        with pytest.raises(HTTPException) as error:
            await service.evaluate_patient_gate(db, denied, patient_id, request)
        assert error.value.status_code == 403
    await db.rollback()
    await db.refresh(patient)
    assert (await grant(api, patient, practitioner_id)).status_code == 200
    gate = (await service.evaluate_patient_gate(db, doctor_id, patient_id, request)).gate
    assert gate.decision == "PASS"
    await db.rollback()
    await db.refresh(patient)
    assert (await revoke(api, patient, practitioner_id)).status_code == 200
    consumer = Mock()
    with pytest.raises(HTTPException) as error:
        await service.consume_patient_gate(db, doctor_id, patient_id, request, gate, consumer)
    assert error.value.status_code == 403
    consumer.assert_not_called()
    await db.rollback()


async def test_database_accepted_pending_rejected_history_and_corrections(db, api):
    from tests.test_extraction import decide, extract

    patient, profile = await add_patient(db)
    account_id, patient_id, auth = patient.id, profile.id, headers(patient)
    proposals = (await extract(api, patient_id, auth)).json()["confirmations"]
    assert (await decide(api, patient_id, auth, proposals[0]["confirmation_id"])).status_code == 200
    decision = await db.get(ConfirmationDecision, UUID(proposals[0]["confirmation_id"]))
    before = (decision.status, decision.decided_by_id, decision.accepted_version_id)
    row = version_input(await db.get(ClinicalFactVersion, decision.accepted_version_id))
    pending, rejected = (
        candidate(patient_id, account_id),
        candidate(patient_id, account_id, status="REJECTED"),
    )
    db.add_all([pending, rejected])
    await db.commit()
    rows_before = {
        r.id: version_input(r).model_dump_json()
        for r in await db.scalars(select(ClinicalFactVersion))
    }
    request = composed(claim_for(row), bound=False)
    gate = (await service.evaluate_patient_gate(db, account_id, patient_id, request)).gate
    assert gate.decision == "PASS"
    for missing in (pending.id, rejected.id):
        refused = await service.evaluate_patient_gate(
            db,
            account_id,
            patient_id,
            composed(claim_for(row, fact_version_ids=(missing,)), bound=False),
        )
        assert refused.gate.decision == "REFUSE" and not refused.gate.rendered_records
    assert before == (decision.status, decision.decided_by_id, decision.accepted_version_id)
    assert rows_before == {
        r.id: version_input(r).model_dump_json()
        for r in await db.scalars(select(ClinicalFactVersion))
    }
    assert not db.new and not db.dirty and not db.deleted
    response = await api.post(
        f"{path(patient_id)}/{row.fact_id}/corrections",
        headers=auth,
        json={
            **entry("7.4"),
            "expected_version_id": str(row.version_id),
            "correction_reason": "synthetic correction",
        },
    )
    assert response.status_code == 201
    assert (
        await service.evaluate_patient_gate(db, account_id, patient_id, request)
    ).gate.decision == "REFUSE"
    consumer = Mock()
    with pytest.raises(HTTPException) as error:
        await service.consume_patient_gate(db, account_id, patient_id, request, gate, consumer)
    assert error.value.status_code == 409
    consumer.assert_not_called()
    await db.rollback()


@pytest.mark.parametrize("revoke_first", [True, False])
async def test_database_revocation_race_and_protected_consumption(
    db, race_environment, revoke_first
):
    env = race_environment
    env["hold_revoke"] = revoke_first
    account = decode_access_token(env["doctor_auth"]["Authorization"].split()[1])
    row = version_input(
        await db.scalar(
            select(ClinicalFactVersion).where(ClinicalFactVersion.patient_id == env["patient_id"])
        )
    )
    request = composed(claim_for(row))
    async with (
        AsyncSession(env["engine"], expire_on_commit=False) as session,
        AsyncClient(transport=ASGITransport(app=env["app"]), base_url="http://test") as client,
    ):

        async def checking():
            env["pids"]["clinical"] = await session.scalar(text("SELECT pg_backend_pid()"))
            return await service.evaluate_patient_gate(session, account, env["patient_id"], request)

        async def revoking():
            return await client.post(
                f"/api/v1/patients/me/consents/{env['doctor_profile_id']}/revoke",
                headers=env["auth"],
            )

        if revoke_first:
            revocation = asyncio.create_task(revoking())
            await asyncio.wait_for(env["commit_ready"].wait(), 3)
            evaluation = asyncio.create_task(checking())
            try:
                await wait_until_blocked(db, env, "clinical")
            finally:
                env["commit_release"].set()
            assert (await revocation).status_code == 200
            with pytest.raises(HTTPException) as error:
                await evaluation
            assert error.value.status_code == 403
            await session.rollback()
        else:
            gate = (await checking()).gate
            revocation = asyncio.create_task(revoking())
            try:
                await wait_until_blocked(db, env, "revoke")
                consumer = Mock()
                await service.consume_patient_gate(
                    session, account, env["patient_id"], request, gate, consumer
                )
                consumer.assert_called_once()
                assert not revocation.done()
            finally:
                await session.rollback()
            assert (await revocation).status_code == 200


async def test_database_concurrent_correction_and_delayed_pass(db, race_environment):
    env = race_environment
    patient_id = env["patient_id"]
    account = decode_access_token(env["doctor_auth"]["Authorization"].split()[1])
    row = version_input(
        await db.scalar(
            select(ClinicalFactVersion).where(ClinicalFactVersion.patient_id == patient_id)
        )
    )
    request = composed(claim_for(row))
    async with (
        AsyncSession(env["engine"], expire_on_commit=False) as session,
        AsyncClient(transport=ASGITransport(app=env["app"]), base_url="http://test") as client,
    ):
        gate = (await service.evaluate_patient_gate(session, account, patient_id, request)).gate
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
            await service.consume_patient_gate(
                session, account, patient_id, request, gate, consumer
            )
            consumer.assert_called_once()
            assert not correcting.done()
        finally:
            await session.rollback()
        assert (await correcting).status_code == 201
        consumer.reset_mock()
        with pytest.raises(HTTPException) as error:
            await service.consume_patient_gate(
                session, account, patient_id, request, gate, consumer
            )
        assert error.value.status_code == 409
        consumer.assert_not_called()
        await session.rollback()


async def test_database_catalogue_change_before_consumption(db, api, monkeypatch):
    patient, profile = await add_patient(db)
    account_id, patient_id = patient.id, profile.id
    response = (await api.post(path(patient_id), headers=headers(patient), json=entry())).json()
    row = version_input(await db.get(ClinicalFactVersion, UUID(response["version_id"])))
    request = composed(claim_for(row))
    gate = (await service.evaluate_patient_gate(db, account_id, patient_id, request)).gate
    monkeypatch.setattr(evidence_service, "catalogue_fingerprint", lambda: None)
    consumer = Mock()
    with pytest.raises(HTTPException) as error:
        await service.consume_patient_gate(db, account_id, patient_id, request, gate, consumer)
    assert error.value.status_code == 503
    consumer.assert_not_called()
    await db.rollback()
