"""Offline DailyMed retrieval, authenticity, and isolation tests.

Network is always mocked. These tests never contact DailyMed or modify patient data.
"""

from datetime import UTC, datetime, timedelta
from xml.etree.ElementTree import Element, SubElement, tostring

import httpx
import pytest

from app.services import dailymed_client as dailymed

REAL_CLIENT = httpx.AsyncClient
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def isolate_cache():
    cache: dict[str, dailymed._CacheEntry] = {}
    dailymed.clear_cache()
    yield cache
    dailymed.clear_cache()


def install_transport(monkeypatch, handler):
    original = REAL_CLIENT

    def factory(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(dailymed.httpx, "AsyncClient", factory)


NS = "{" + dailymed.SPL_NAMESPACE + "}"


def el(parent, name, **attrib):
    if parent is None:
        return Element(NS + name, attrib)
    return SubElement(parent, NS + name, attrib)


def spl_xml(
    *,
    set_id: str,
    title: str,
    version: str = "12",
    effective: str = "20240115",
    indications: str | None = "Indicated as an adjunct to diet and exercise.",
    contraindications: str | None = "Severe renal impairment (eGFR below 30).",
    warnings: str | None = "Boxed warning: lactic acidosis has been reported.",
    precautions: str | None = "Monitor renal function before and during treatment.",
    renal: str | None = "Do not use if eGFR is below 30 mL/min/1.73 m2.",
    namespace: str = dailymed.SPL_NAMESPACE,
) -> bytes:
    ns = "{" + namespace + "}"
    document = Element(ns + "document")

    def child(parent, name, **attrib):
        return SubElement(parent, ns + name, attrib)

    child(document, "id", root="11111111-1111-1111-1111-111111111111")
    child(
        document,
        "code",
        code="34391-3",
        codeSystem="2.16.840.1.113883.6.1",
        displayName="HUMAN PRESCRIPTION DRUG LABEL",
    )
    child(document, "title").text = title
    child(document, "effectiveTime", value=effective)
    child(document, "setId", root=set_id)
    child(document, "versionNumber", value=version)
    body = child(child(document, "component"), "structuredBody")

    def add_section(parent, code: str, display: str, heading: str, text: str | None) -> None:
        if text is None:
            return
        section = child(child(parent, "component"), "section")
        child(section, "id", root=code)
        child(
            section,
            "code",
            code=code,
            codeSystem="2.16.840.1.113883.6.1",
            displayName=display,
        )
        child(section, "title").text = heading
        child(child(section, "text"), "paragraph").text = text

    add_section(body, "34067-9", "INDICATIONS AND USAGE", "1 INDICATIONS AND USAGE", indications)
    add_section(body, "34070-3", "CONTRAINDICATIONS", "4 CONTRAINDICATIONS", contraindications)
    add_section(body, "34071-1", "WARNINGS", "5 WARNINGS", warnings)
    add_section(body, "42232-9", "PRECAUTIONS", "5 PRECAUTIONS", precautions)
    populations = child(child(body, "component"), "section")
    child(
        populations,
        "code",
        code="43684-0",
        codeSystem="2.16.840.1.113883.6.1",
        displayName="USE IN SPECIFIC POPULATIONS",
    )
    child(populations, "title").text = "8 USE IN SPECIFIC POPULATIONS"
    add_section(
        populations,
        "42227-9",
        "RENAL IMPAIRMENT SUBSECTION",
        "8.6 Renal Impairment",
        renal,
    )
    return tostring(document, encoding="utf-8", xml_declaration=True)


def metformin_body(**changes) -> bytes:
    pin = dailymed.prototype_labels()[0]
    assert pin.ingredient_name == "metformin"
    return spl_xml(set_id=pin.set_id, title="GLUCOPHAGE (metformin hydrochloride) tablet", **changes)


def handler_for(bodies: dict[str, bytes] | bytes, status: int = 200):
    if isinstance(bodies, bytes):
        mapping = {label.set_id: bodies for label in dailymed.prototype_labels()}
    else:
        mapping = bodies

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.scheme == "https"
        assert request.url.host == "dailymed.nlm.nih.gov"
        path = request.url.path
        for set_id, body in mapping.items():
            if path.endswith(f"/spls/{set_id}.xml"):
                return httpx.Response(status, content=body)
        return httpx.Response(404, content=b"missing")

    return handler


def section_map(label: dailymed.DailyMedLabel) -> dict[str, dailymed.DailyMedSection]:
    return {item.name: item for item in label.sections}


async def test_metformin_label_preserves_source_identity(monkeypatch, isolate_cache):
    install_transport(monkeypatch, handler_for(metformin_body()))
    label = await dailymed.retrieve_label("metformin", now=NOW, cache=isolate_cache)
    pin = next(item for item in dailymed.prototype_labels() if item.ingredient_name == "metformin")
    assert label.status == "RESOLVED"
    assert label.reason_code == "LABEL_RETRIEVED"
    assert label.set_id == pin.set_id
    assert label.spl_version == "12"
    assert label.published_date == "2024-01-15"
    assert label.retrieved_at == NOW
    assert label.canonical_url == (
        f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={pin.set_id}"
    )
    assert label.api_url == (
        f"https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/{pin.set_id}.xml"
    )
    assert "metformin" in label.title.lower()
    found = section_map(label)
    assert found["indications"].status == "PRESENT"
    assert found["contraindications"].status == "PRESENT"
    assert found["warnings"].status == "PRESENT"
    assert found["precautions"].status == "PRESENT"
    assert found["renal_considerations"].status == "PRESENT"
    assert "eGFR" in found["renal_considerations"].content
    assert "adjunct" in found["indications"].content


@pytest.mark.parametrize("name", ["metformin", "empagliflozin", "semaglutide"])
async def test_each_prototype_ingredient_resolves(monkeypatch, isolate_cache, name):
    pin = next(item for item in dailymed.prototype_labels() if item.ingredient_name == name)
    body = spl_xml(set_id=pin.set_id, title=f"{name} tablet")
    install_transport(monkeypatch, handler_for({pin.set_id: body}))
    label = await dailymed.retrieve_label(name.upper(), now=NOW, cache=isolate_cache)
    assert label.status == "RESOLVED"
    assert label.rxcui == pin.rxcui
    assert label.set_id == pin.set_id


async def test_missing_section_is_unavailable_not_absent_warning(monkeypatch, isolate_cache):
    install_transport(
        monkeypatch,
        handler_for(metformin_body(warnings=None, precautions=None, renal=None)),
    )
    label = await dailymed.retrieve_label("metformin", now=NOW, cache=isolate_cache)
    found = section_map(label)
    assert label.status == "RESOLVED"
    assert found["indications"].status == "PRESENT"
    assert found["contraindications"].status == "PRESENT"
    assert found["warnings"].status == "UNAVAILABLE"
    assert found["precautions"].status == "UNAVAILABLE"
    assert found["renal_considerations"].status == "UNAVAILABLE"
    assert found["warnings"].content == ""
    assert "ABSENT" not in found["warnings"].status
    units = dailymed.as_evidence_units(label)
    assert {unit.locator for unit in units} == {
        "section:indications",
        "section:contraindications",
    }


async def test_unknown_ingredient_does_not_call_network(monkeypatch, isolate_cache):
    def fail(request):
        raise AssertionError(f"unexpected request {request.url}")

    install_transport(monkeypatch, fail)
    label = await dailymed.retrieve_label("atorvastatin", now=NOW, cache=isolate_cache)
    assert label.status == "NOT_FOUND"
    assert label.reason_code == "PROTOTYPE_INGREDIENT_UNKNOWN"
    assert label.set_id == ""


async def test_unpinned_setid_rejected():
    with pytest.raises(ValueError, match="allowlist"):
        dailymed.approved_spl_url("0fdd0255-0055-65f3-b2c0-db8fbb87beae")  # Synjardy combo


async def test_non_uuid_setid_rejected():
    with pytest.raises(ValueError, match="UUID"):
        dailymed.approved_spl_url("https://evil.example/spl.xml")


async def test_wrong_setid_in_xml_is_authenticity_failure(monkeypatch, isolate_cache):
    pin = next(item for item in dailymed.prototype_labels() if item.ingredient_name == "metformin")
    body = spl_xml(
        set_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        title="GLUCOPHAGE (metformin hydrochloride) tablet",
    )
    install_transport(monkeypatch, handler_for({pin.set_id: body}))
    label = await dailymed.retrieve_label("metformin", now=NOW, cache=isolate_cache)
    assert label.status == "ERROR"
    assert label.reason_code == "AUTHENTICITY_FAILURE"
    assert all(section.status == "UNAVAILABLE" for section in label.sections)


async def test_wrong_ingredient_title_is_authenticity_failure(monkeypatch, isolate_cache):
    pin = next(item for item in dailymed.prototype_labels() if item.ingredient_name == "metformin")
    body = spl_xml(set_id=pin.set_id, title="ATORVASTATIN tablet")
    install_transport(monkeypatch, handler_for({pin.set_id: body}))
    label = await dailymed.retrieve_label("metformin", now=NOW, cache=isolate_cache)
    assert label.status == "ERROR"
    assert label.reason_code == "AUTHENTICITY_FAILURE"


async def test_malformed_xml_is_error(monkeypatch, isolate_cache):
    pin = next(item for item in dailymed.prototype_labels() if item.ingredient_name == "metformin")
    install_transport(monkeypatch, handler_for({pin.set_id: b"<not-xml"}))
    label = await dailymed.retrieve_label("metformin", now=NOW, cache=isolate_cache)
    assert label.status == "ERROR"
    assert label.reason_code == "MALFORMED_RESPONSE"


async def test_http_404_is_unavailable(monkeypatch, isolate_cache):
    install_transport(monkeypatch, handler_for(metformin_body(), status=404))
    label = await dailymed.retrieve_label("metformin", now=NOW, cache=isolate_cache)
    assert label.status == "UNAVAILABLE"
    assert label.reason_code == "HTTP_UNAVAILABLE"


async def test_http_500_is_error(monkeypatch, isolate_cache):
    install_transport(monkeypatch, handler_for(metformin_body(), status=500))
    label = await dailymed.retrieve_label("metformin", now=NOW, cache=isolate_cache)
    assert label.status == "ERROR"
    assert label.reason_code == "HTTP_ERROR"


async def test_timeout_is_error(monkeypatch, isolate_cache):
    def handler(request):
        raise httpx.ReadTimeout("timeout")

    install_transport(monkeypatch, handler)
    label = await dailymed.retrieve_label("metformin", now=NOW, cache=isolate_cache)
    assert label.status == "ERROR"
    assert label.reason_code == "TIMEOUT"


async def test_network_failure_is_error(monkeypatch, isolate_cache):
    def handler(request):
        raise httpx.ConnectError("down")

    install_transport(monkeypatch, handler)
    label = await dailymed.retrieve_label("metformin", now=NOW, cache=isolate_cache)
    assert label.status == "ERROR"
    assert label.reason_code == "NETWORK_FAILURE"


async def test_oversized_response_rejected(monkeypatch, isolate_cache):
    pin = next(item for item in dailymed.prototype_labels() if item.ingredient_name == "metformin")
    install_transport(monkeypatch, handler_for({pin.set_id: b"x" * (dailymed._MAX_BYTES + 1)}))
    label = await dailymed.retrieve_label("metformin", now=NOW, cache=isolate_cache)
    assert label.status == "ERROR"
    assert label.reason_code == "RESPONSE_TOO_LARGE"


async def test_redirect_not_followed(monkeypatch, isolate_cache):
    def handler(request):
        return httpx.Response(302, headers={"location": "https://evil.example/label.xml"})

    install_transport(monkeypatch, handler)
    label = await dailymed.retrieve_label("metformin", now=NOW, cache=isolate_cache)
    assert label.status == "ERROR"
    assert label.reason_code == "HTTP_ERROR"


async def test_fresh_cache_skips_network(monkeypatch, isolate_cache):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, content=metformin_body())

    install_transport(monkeypatch, handler)
    first = await dailymed.retrieve_label("metformin", now=NOW, cache=isolate_cache)
    second = await dailymed.retrieve_label(
        "metformin", now=NOW + timedelta(hours=1), cache=isolate_cache
    )
    assert first.status == second.status == "RESOLVED"
    assert calls["n"] == 1
    assert second.retrieved_at == NOW


async def test_stale_cache_returned_when_refresh_fails(monkeypatch, isolate_cache):
    def ok(request):
        return httpx.Response(200, content=metformin_body())

    install_transport(monkeypatch, ok)
    original = await dailymed.retrieve_label("metformin", now=NOW, cache=isolate_cache)
    assert original.status == "RESOLVED"

    def down(request):
        raise httpx.ConnectError("down")

    install_transport(monkeypatch, down)
    stale = await dailymed.retrieve_label(
        "metformin", now=NOW + timedelta(hours=25), cache=isolate_cache
    )
    assert stale.status == "STALE"
    assert stale.reason_code == "CACHE_STALE"
    assert stale.set_id == original.set_id
    assert section_map(stale)["contraindications"].content == (
        section_map(original)["contraindications"].content
    )


async def test_evidence_projection_uses_trusted_publisher(monkeypatch, isolate_cache):
    install_transport(monkeypatch, handler_for(metformin_body()))
    label = await dailymed.retrieve_label("metformin", now=NOW, cache=isolate_cache)
    source = dailymed.as_evidence_source(label)
    assert source is not None
    assert source.publisher == "U.S. National Library of Medicine / DailyMed"
    assert source.label == "REAL_CLINICAL_EVIDENCE"
    assert source.canonical_location == label.canonical_url
    units = dailymed.as_evidence_units(label)
    assert units
    assert all(unit.label == "REAL_CLINICAL_EVIDENCE" for unit in units)
    assert all(unit.locator.startswith("section:") for unit in units)


async def test_unresolved_label_does_not_become_evidence():
    label = await dailymed.retrieve_label("insulin")
    assert label.status == "NOT_FOUND"
    assert dailymed.as_evidence_source(label) is None
    assert dailymed.as_evidence_units(label) == ()


async def test_result_has_no_prescribing_fields(monkeypatch, isolate_cache):
    install_transport(monkeypatch, handler_for(metformin_body()))
    label = await dailymed.retrieve_label("metformin", now=NOW, cache=isolate_cache)
    assert not hasattr(label, "dose")
    assert not hasattr(label, "recommendation")
    assert not hasattr(label, "prescription")


async def test_dosage_section_is_not_treated_as_renal_considerations():
    pin = next(item for item in dailymed.prototype_labels() if item.ingredient_name == "metformin")
    document_xml = spl_xml(
        set_id=pin.set_id,
        title="metformin tablet",
        contraindications=None,
        warnings=None,
        precautions=None,
        renal=None,
    )
    # Append a dosage section mentioning eGFR; it must not become renal evidence.
    from xml.etree.ElementTree import fromstring

    root = fromstring(document_xml)
    body = None
    for elem in root.iter():
        if elem.tag.endswith("structuredBody"):
            body = elem
            break
    assert body is not None
    section = el(el(body, "component"), "section")
    el(section, "code", code="34068-7", codeSystem="2.16.840.1.113883.6.1")
    el(section, "title").text = "2 DOSAGE AND ADMINISTRATION"
    el(el(section, "text"), "paragraph").text = "Renal dosing: start 500 mg if eGFR is 45."
    label = dailymed.parse_spl(tostring(root), pin, NOW)
    found = section_map(label)
    assert found["renal_considerations"].status == "UNAVAILABLE"
    assert found["indications"].status == "PRESENT"


async def test_combined_warnings_and_precautions_loinc(monkeypatch, isolate_cache):
    pin = next(item for item in dailymed.prototype_labels() if item.ingredient_name == "semaglutide")
    body = spl_xml(
        set_id=pin.set_id,
        title="OZEMPIC (semaglutide) injection",
        warnings=None,
        precautions=None,
        renal=None,
    )
    from xml.etree.ElementTree import fromstring

    root = fromstring(body)
    body_el = None
    for elem in root.iter():
        if elem.tag.endswith("structuredBody"):
            body_el = elem
            break
    section = el(el(body_el, "component"), "section")
    el(section, "code", code="43685-7", codeSystem="2.16.840.1.113883.6.1")
    el(section, "title").text = "5 WARNINGS AND PRECAUTIONS"
    el(el(section, "text"), "paragraph").text = "Risk of thyroid C-cell tumors. Pancreatitis."
    install_transport(monkeypatch, handler_for({pin.set_id: tostring(root)}))
    label = await dailymed.retrieve_label("semaglutide", now=NOW, cache=isolate_cache)
    found = section_map(label)
    assert found["warnings"].status == "PRESENT"
    assert found["precautions"].status == "PRESENT"
    assert "thyroid" in found["warnings"].content.lower()


def test_no_patient_data_imported():
    import ast
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "app" / "services" / "dailymed_client.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
    forbidden = {
        "sqlalchemy",
        "app.models",
        "app.core.database",
        "app.services.authorization",
        "app.services.clinical_facts",
        "app.core.config",
        "fastapi",
        "app.services.evidence_catalog",
    }
    for item in forbidden:
        assert not any(module == item or module.startswith(item + ".") for module in imported), (
            f"dailymed_client imports forbidden module {item!r}"
        )


def test_prototype_pins_are_single_ingredient():
    names = {item.ingredient_name for item in dailymed.prototype_labels()}
    assert names == {"metformin", "empagliflozin", "semaglutide"}
    set_ids = [item.set_id for item in dailymed.prototype_labels()]
    assert len(set_ids) == len(set(set_ids))
    # Combination product Synjardy must not be pinned.
    assert "0fdd0255-0055-65f3-b2c0-db8fbb87beae" not in set_ids


async def test_wrong_namespace_is_authenticity_failure():
    pin = next(item for item in dailymed.prototype_labels() if item.ingredient_name == "metformin")
    body = spl_xml(
        set_id=pin.set_id,
        title="metformin tablet",
        namespace="http://example.invalid",
    )
    label = dailymed.parse_spl(body, pin, NOW)
    assert label.status == "ERROR"
    assert label.reason_code == "AUTHENTICITY_FAILURE"
