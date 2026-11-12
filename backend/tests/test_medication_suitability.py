"""Synthetic-only offline safety coverage; no database or external label calls."""

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import current_account
from app.api.v1 import clinical_facts
from app.core.config import Settings, settings
from app.core.database import get_db
from app.main import create_app
from app.schemas.consistency import FactVersion
from app.services import medication_suitability as service
from app.services.consistency_evaluator import ConsistencyInputError
from app.services.extraction_provider import mock_output, validate_candidates

PATIENT, OTHER, ACTOR = UUID(int=1), UUID(int=2), UUID(int=3)
WHEN = datetime(2026, 2, 1, tzinfo=UTC)
NOW = datetime(2026, 11, 12, tzinfo=UTC)


def fact(number, payload, **changes):
    return FactVersion.model_validate(dict(
        version_id=UUID(int=number), fact_id=UUID(int=number), patient_id=PATIENT,
        version=1, attribution="practitioner_managed", author_id=ACTOR,
        author_role="practitioner", fact=payload, observed_at=WHEN, entered_at=WHEN,
        provenance={"source": "Synthetic capstone test", "reference": None, "data_origin": "synthetic"},
        **changes,
    ))


def records(egfr="42"):
    result = [fact(10, {"kind": "diagnosis", "code": "type_2_diabetes"}),
              fact(11, {"kind": "laboratory", "code": "hba1c", "value": "7.6", "unit": "%"}),
              fact(12, {"kind": "medication", "medication_name": "metformin"})]
    if egfr is not None:
        result.append(fact(13, {"kind": "laboratory", "code": "egfr", "value": egfr, "unit": "mL/min/1.73m2"}))
    return result


def assess(rows, history=None, catalog=True):
    return service.assess_records(PATIENT, rows, rows if history is None else history,
                                  service.load_catalog() if catalog else None, now=NOW)


@pytest.mark.parametrize("value,metformin,empagliflozin", [
    ("25", "NOT_SUITABLE_UNDER_DOCUMENTED_RULE", "CAUTION_REVIEW_REQUIRED"),
    ("29.999", "NOT_SUITABLE_UNDER_DOCUMENTED_RULE", "CAUTION_REVIEW_REQUIRED"),
    ("30", "CAUTION_REVIEW_REQUIRED", "INSUFFICIENT_INFORMATION"),
    ("42", "CAUTION_REVIEW_REQUIRED", "INSUFFICIENT_INFORMATION"),
    ("45", "CAUTION_REVIEW_REQUIRED", "INSUFFICIENT_INFORMATION"),
    ("45.001", "INSUFFICIENT_INFORMATION", "INSUFFICIENT_INFORMATION"),
    ("90", "INSUFFICIENT_INFORMATION", "INSUFFICIENT_INFORMATION"),
    ("0", "INSUFFICIENT_INFORMATION", "INSUFFICIENT_INFORMATION"),
    ("-1", "INSUFFICIENT_INFORMATION", "INSUFFICIENT_INFORMATION"),
    (None, "INSUFFICIENT_INFORMATION", "INSUFFICIENT_INFORMATION"),
])
def test_renal_boundaries_without_clearance(value, metformin, empagliflozin):
    result = assess(records(value))
    assert [c.status for c in result.assessments] == [metformin, empagliflozin, "INSUFFICIENT_INFORMATION"]
    assert all(c.clinician_review_required for c in result.assessments)
    assert result.confirmation_scope == "SPEAKER_CONFIRMED_NOT_CLINICIAN_APPROVED"
    assert "glycemic" in result.assessments[1].rule_checks[0].rule.scope.lower()


@pytest.mark.parametrize("category,positive,negative,label", service.HISTORY_FIELDS)
def test_exact_contraindication_and_negation(category, positive, negative, label):
    rows = records("70")
    marker = fact(20, {"kind": "history", "category": category, "description": positive})
    result = assess([*rows, marker])
    assert result.assessments[2].status == "NOT_SUITABLE_UNDER_DOCUMENTED_RULE"
    check = result.assessments[2].rule_checks[0]
    assert marker.version_id in check.fact_version_ids and check.outcome == "TRIGGERED"
    for unsupported in (negative, "possible " + positive, "denies " + positive):
        row = marker.model_copy(update={"fact": marker.fact.model_copy(update={"description": unsupported})})
        assert assess([*rows, row]).assessments[2].status == "INSUFFICIENT_INFORMATION"


def test_conflicting_positive_and_negative_history_is_unknown():
    rows = records()
    for n, phrase in enumerate(("personal history of medullary thyroid carcinoma", "no personal history of medullary thyroid carcinoma"), 20):
        rows.append(fact(n, {"kind": "history", "category": "prior_event", "description": phrase}))
    card = assess(rows).assessments[2]
    assert card.status == "INSUFFICIENT_INFORMATION"
    assert card.rule_checks[0].outcome == "UNKNOWN"
    assert any("conflicting" in text for text in card.missing_information)


def test_conflicting_latest_lab_never_picks_convenient_value():
    rows = records("25")
    rows.append(fact(14, {"kind": "laboratory", "code": "egfr", "value": "90", "unit": "mL/min/1.73m2"}))
    result = assess(rows)
    assert all(card.status == "INSUFFICIENT_INFORMATION" for card in result.assessments)
    assert any("conflicting latest" in text for text in result.assessments[0].missing_information)


def test_correction_replaces_old_value_and_preserves_exact_provenance():
    old = records("25")
    updated = old[-1].model_copy(update={
        "version_id": UUID(int=14), "version": 2, "previous_version_id": old[-1].version_id,
        "previous_version": 1, "correction_reason": "synthetic correction",
        "entered_at": datetime(2026, 3, 1, tzinfo=UTC),
        "fact": old[-1].fact.model_copy(update={"value": Decimal("42")}),
    })
    # Revalidate Decimal and structural types, just as the persisted adapter does.
    updated = FactVersion.model_validate(updated.model_dump())
    result = assess([*old[:-1], updated], [*old, updated])
    assert result.assessments[0].status == "CAUTION_REVIEW_REQUIRED"
    assert old[-1].version_id not in {r.version_id for r in result.facts}
    assert result.assessments[0].rule_checks[0].fact_version_ids == (updated.version_id,)
    assert next(r for r in result.facts if r.version_id == updated.version_id).provenance == updated.provenance
    with pytest.raises(ConsistencyInputError):
        assess(old, [*old, updated])


def test_latest_observation_uses_observation_time_not_entry_time():
    rows = records("25")
    later = fact(14, {"kind": "laboratory", "code": "egfr", "value": "42", "unit": "mL/min/1.73m2"})
    later = later.model_copy(update={"observed_at": datetime(2026, 3, 1, tzinfo=UTC), "entered_at": datetime(2026, 3, 1, tzinfo=UTC)})
    rows[-1] = rows[-1].model_copy(update={"entered_at": datetime(2026, 4, 1, tzinfo=UTC)})
    assert assess([*rows, later]).assessments[0].status == "CAUTION_REVIEW_REQUIRED"


@pytest.mark.parametrize("case", ["foreign", "pending", "rejected", "nonsynthetic"])
def test_invalid_or_foreign_context_fails_closed(case):
    rows = records()
    changes = {"foreign": {"patient_id": OTHER}, "pending": {"status": "PENDING"},
               "rejected": {"status": "REJECTED"}, "nonsynthetic": {"provenance": rows[0].provenance.model_copy(update={"data_origin": "de_identified"})}}
    rows[0] = rows[0].model_copy(update=changes[case])
    with pytest.raises(ConsistencyInputError):
        assess(rows)


def test_missing_diagnosis_empty_and_future_context_never_trigger_rules():
    cases = [[], records()[1:]]
    future = records("25")
    future[-1] = future[-1].model_copy(update={"observed_at": datetime(2027, 1, 1, tzinfo=UTC), "entered_at": datetime(2027, 1, 1, tzinfo=UTC)})
    cases.append(future)
    for rows in cases:
        assert all(c.status == "INSUFFICIENT_INFORMATION" for c in assess(rows).assessments)


def test_evidence_absent_never_emits_label_verdict():
    result = assess(records("25"), catalog=False)
    assert result.evidence_review_status == "UNAVAILABLE"
    assert result.catalog_sha256 is None
    assert all(c.status == "INSUFFICIENT_INFORMATION" and not c.evidence and not c.rule_checks for c in result.assessments)


def test_catalog_integrity_and_source_traceability(monkeypatch, tmp_path):
    catalog = service.load_catalog()
    assert catalog is not None
    assert hashlib.sha256(service.CATALOG_PATH.read_bytes()).hexdigest() == service.CATALOG_SHA256
    assert {rule.source_id for rule in catalog.rules} == {source.source_id for source in catalog.sources}
    assert all(source.url.startswith("https://dailymed.nlm.nih.gov/") for source in catalog.sources)
    path = tmp_path / "synthetic-catalog.json"
    path.write_text('{"catalog_version": "forged"}')
    monkeypatch.setattr(service, "CATALOG_PATH", path)
    assert service.load_catalog() is None
    monkeypatch.setattr(service, "CATALOG_PATH", tmp_path / "missing.json")
    assert service.load_catalog() is None


@pytest.fixture
def harness(monkeypatch):
    rows = records()
    db = SimpleNamespace(scalar=AsyncMock(return_value=True), add=Mock(), flush=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())
    scope = AsyncMock(return_value=SimpleNamespace(role="practitioner"))
    loader = AsyncMock(return_value=(rows, rows))
    monkeypatch.setattr(service, "lock_patient_scope", scope)
    monkeypatch.setattr(service, "load_accepted_state", loader)
    return SimpleNamespace(db=db, scope=scope, loader=loader)


@pytest.mark.parametrize("denial", [401, 403])
async def test_authorization_or_consent_denial_precedes_data_access(harness, denial):
    harness.scope.side_effect = HTTPException(denial)
    with pytest.raises(HTTPException) as caught:
        await service.patient_assessment(harness.db, ACTOR, PATIENT)
    assert caught.value.status_code == denial
    harness.db.scalar.assert_not_awaited()
    harness.loader.assert_not_awaited()


@pytest.mark.parametrize("role,demo,status", [("patient", True, 403), ("practitioner", False, 409)])
async def test_only_practitioner_owned_synthetic_context(harness, role, demo, status):
    harness.scope.return_value = SimpleNamespace(role=role)
    harness.db.scalar.return_value = demo
    with pytest.raises(HTTPException) as caught:
        await service.patient_assessment(harness.db, ACTOR, PATIENT)
    assert caught.value.status_code == status
    harness.loader.assert_not_awaited()


async def test_service_is_scoped_and_does_not_write(harness):
    result = await service.patient_assessment(harness.db, ACTOR, PATIENT)
    assert result.patient_id == PATIENT
    harness.scope.assert_awaited_once_with(harness.db, ACTOR, PATIENT)
    harness.loader.assert_awaited_once_with(harness.db, ACTOR, PATIENT)
    assert PATIENT in harness.db.scalar.call_args.args[0].compile().params.values()
    harness.db.add.assert_not_called()
    harness.db.flush.assert_not_awaited()
    harness.db.commit.assert_not_awaited()


async def test_http_authentication():
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        assert (await client.get(f"/api/v1/patients/{PATIENT}/medication-suitability")).status_code == 401


async def test_http_protected_release_and_revocation(harness, monkeypatch):
    app = create_app()
    app.dependency_overrides[current_account] = lambda: SimpleNamespace(id=ACTOR)

    async def database():
        yield harness.db

    app.dependency_overrides[get_db] = database
    lock = AsyncMock()
    monkeypatch.setattr(clinical_facts, "lock_patient_scope", lock)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/patients/{PATIENT}/medication-suitability")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    lock.assert_awaited_once_with(harness.db, ACTOR, PATIENT)
    # Release-time denial prevents any ASGI body from being sent.
    lock.side_effect = HTTPException(403)
    send = AsyncMock()
    body = clinical_facts.ClinicalResponse(harness.db, ACTOR, PATIENT, assess(records()))
    with pytest.raises(HTTPException):
        await body({"type": "http"}, AsyncMock(), send)
    send.assert_not_awaited()


@pytest.mark.parametrize("enabled", [False, True])
async def test_m8_routes_explicitly_gated(monkeypatch, enabled):
    assert Settings.model_fields["ENABLE_CLINICIAN_FACT_REVIEW"].default is False
    monkeypatch.setattr(settings, "ENABLE_CLINICIAN_FACT_REVIEW", enabled)
    app = create_app()
    paths = app.openapi()["paths"]
    assert ("/api/v1/patients/{patient_id}/clinical-fact-reviews" in paths) is enabled
    assert "/api/v1/patients/{patient_id}/medication-suitability" in paths
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.get(f"/api/v1/patients/{PATIENT}/clinical-fact-reviews")
    assert result.status_code == (401 if enabled else 404)


@pytest.mark.parametrize("scenario,count,expected", [
    ("A", 6, ["CAUTION_REVIEW_REQUIRED", "INSUFFICIENT_INFORMATION", "INSUFFICIENT_INFORMATION"]),
    ("B", 4, ["NOT_SUITABLE_UNDER_DOCUMENTED_RULE", "CAUTION_REVIEW_REQUIRED", "NOT_SUITABLE_UNDER_DOCUMENTED_RULE"]),
])
def test_actual_demo_notes_parse_ground_and_produce_expected_outcomes(scenario, count, expected):
    path = Path(__file__).resolve().parents[2] / "docs/demo-review-2026-11-17" / f"PATIENT_{scenario}_NOTE.txt"
    text = path.read_text(encoding="utf-8")
    proposals = validate_candidates(mock_output(text), text)
    assert len(proposals) == count
    rows = []
    for number, proposal in enumerate(proposals, 100):
        # The demonstration explicitly rejects A's incorrect symptom proposal.
        if proposal.fact.kind == "symptom":
            continue
        row = fact(number, proposal.fact.model_dump()).model_copy(update={
            "observed_at": proposal.observed_at, "entered_at": NOW,
            "entry_method": "LLM_EXTRACTED", "confirmed_by_id": ACTOR, "confirmed_at": NOW,
        })
        rows.append(row)
    result = assess(rows)
    assert [c.status for c in result.assessments] == expected
    assert not any(r.fact.kind == "symptom" for r in result.facts)
