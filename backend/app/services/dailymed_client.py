"""Official DailyMed SPL retrieval for the ClinCoherence T2DM prototype vocabulary.

Retrieves FDA labeling for metformin, empagliflozin, and semaglutide from the
NLM DailyMed REST API v2. This is a read-only evidence adapter: it never writes
patient records, never ranks or recommends medications, and never treats a
missing label section as proof that a warning does not exist.

Approved endpoints (HTTPS only, no redirects, no environment proxy):
  GET https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/{SETID}.xml

Human-readable canonical location (not fetched):
  https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={SETID}

SET IDs are pinned in code for the three prototype ingredients. An HTTPS URL or
publisher string alone is not accepted as authenticity. Arbitrary DailyMed
search results and combination products are out of scope.

DailyMed content is U.S. government work. Retrieved text is labeling evidence
for practitioner review, not a prescription or dosing instruction.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Literal
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from app.schemas.evidence import EvidenceSource, EvidenceUnit

DAILYMED_ORIGIN: Final[str] = "https://dailymed.nlm.nih.gov"
DAILYMED_PUBLISHER: Final[str] = "U.S. National Library of Medicine / DailyMed"
SPL_NAMESPACE: Final[str] = "urn:hl7-org:v3"
SETID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_TIMEOUT_SECONDS: Final[float] = 10.0
_CONNECT_SECONDS: Final[float] = 3.0
_MAX_BYTES: Final[int] = 2_000_000
_CACHE_TTL: Final[timedelta] = timedelta(hours=24)
_UNIT_CONTENT_LIMIT: Final[int] = 4000
_USER_AGENT: Final[str] = "ClinCoherence/0.1 (research prototype; DailyMed SPL retrieval)"

SectionName = Literal[
    "indications",
    "contraindications",
    "warnings",
    "precautions",
    "renal_considerations",
]
LabelStatus = Literal["RESOLVED", "UNAVAILABLE", "ERROR", "STALE", "NOT_FOUND"]
SectionStatus = Literal["PRESENT", "UNAVAILABLE"]

# SPL section LOINC codes (codeSystem 2.16.840.1.113883.6.1).
_SECTION_LOINC: Final[dict[SectionName, frozenset[str]]] = {
    "indications": frozenset({"34067-9"}),
    "contraindications": frozenset({"34070-3"}),
    "warnings": frozenset({"34071-1", "34066-1", "43685-7", "54433-8"}),
    "precautions": frozenset({"42232-9", "43685-7"}),
    "renal_considerations": frozenset({"42227-9"}),
}
_RENAL_TITLE: Final[re.Pattern[str]] = re.compile(
    r"\b(renal|kidney|egfr|glomerular)\b", re.IGNORECASE
)
# Dosage/administration must not be used as a prescribing or renal-dosing source.
_EXCLUDED_LOINC: Final[frozenset[str]] = frozenset({"34068-7"})


@dataclass(frozen=True)
class PrototypeLabel:
    ingredient_name: str
    rxcui: str
    set_id: str
    title_token: str  # must appear in the SPL title (ingredient or brand)


# Pinned originator labels for the prototype vocabulary. Combination products
# (e.g. empagliflozin/metformin) are intentionally absent.
_PROTOTYPE: Final[dict[str, PrototypeLabel]] = {
    "metformin": PrototypeLabel(
        ingredient_name="metformin",
        rxcui="6809",
        set_id="4a0166c7-7097-4e4a-9036-6c9a60d08fc6",
        title_token="metformin",
    ),
    "empagliflozin": PrototypeLabel(
        ingredient_name="empagliflozin",
        rxcui="1544385",
        set_id="a77214ca-572e-485e-aae9-f7950187e5fb",
        title_token="empagliflozin",
    ),
    "semaglutide": PrototypeLabel(
        ingredient_name="semaglutide",
        rxcui="2200644",
        set_id="e6da837c-0da0-41d6-973c-5559bf764367",
        title_token="semaglutide",
    ),
}


@dataclass(frozen=True)
class DailyMedSection:
    """One labeling section. UNAVAILABLE means the section was not retrieved,
    not that the FDA label asserts the warning is absent."""

    name: SectionName
    status: SectionStatus
    locator: str
    loinc_codes: tuple[str, ...]
    content: str
    truncated: bool
    content_hash: str


@dataclass(frozen=True)
class DailyMedLabel:
    """External labeling evidence. Never a patient fact or prescription."""

    ingredient_name: str
    rxcui: str
    set_id: str
    spl_version: str
    published_date: str
    retrieved_at: datetime
    canonical_url: str
    api_url: str
    title: str
    status: LabelStatus
    reason_code: str
    sections: tuple[DailyMedSection, ...]


@dataclass(frozen=True)
class _CacheEntry:
    label: DailyMedLabel
    stored_at: datetime


_CACHE: dict[str, _CacheEntry] = {}


def clear_cache() -> None:
    _CACHE.clear()


def prototype_labels() -> tuple[PrototypeLabel, ...]:
    return tuple(_PROTOTYPE.values())


def approved_spl_url(set_id: str) -> str:
    """Build the only allowed SPL fetch URL. Rejects unknown SET IDs."""
    normalized = set_id.strip().lower()
    if not SETID_PATTERN.fullmatch(normalized):
        raise ValueError("SET ID is not a lowercase UUID")
    if not any(entry.set_id == normalized for entry in _PROTOTYPE.values()):
        raise ValueError("SET ID is not in the prototype DailyMed allowlist")
    return f"{DAILYMED_ORIGIN}/dailymed/services/v2/spls/{normalized}.xml"


def canonical_label_url(set_id: str) -> str:
    normalized = set_id.strip().lower()
    if not SETID_PATTERN.fullmatch(normalized):
        raise ValueError("SET ID is not a lowercase UUID")
    return f"{DAILYMED_ORIGIN}/dailymed/drugInfo.cfm?setid={normalized}"


def _assert_approved_request_url(url: str, set_id: str) -> None:
    parsed = urlsplit(url)
    expected = approved_spl_url(set_id)
    expected_parsed = urlsplit(expected)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "dailymed.nlm.nih.gov"
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.query
        or parsed.port not in {None, 443}
        or url != expected
        or expected_parsed.path != parsed.path
    ):
        raise ValueError("Request URL is not an approved DailyMed SPL endpoint")


def _empty_sections() -> tuple[DailyMedSection, ...]:
    return tuple(
        DailyMedSection(
            name=name,
            status="UNAVAILABLE",
            locator=f"section:{name.replace('_', '-')}",
            loinc_codes=(),
            content="",
            truncated=False,
            content_hash="",
        )
        for name in _SECTION_LOINC
    )


def _failure(
    prototype: PrototypeLabel | None,
    ingredient: str,
    status: LabelStatus,
    reason_code: str,
    retrieved_at: datetime,
    *,
    set_id: str = "",
    title: str = "",
    spl_version: str = "",
    published_date: str = "",
) -> DailyMedLabel:
    pin = prototype
    sid = set_id or (pin.set_id if pin else "")
    return DailyMedLabel(
        ingredient_name=pin.ingredient_name if pin else ingredient,
        rxcui=pin.rxcui if pin else "",
        set_id=sid,
        spl_version=spl_version,
        published_date=published_date,
        retrieved_at=retrieved_at,
        canonical_url=canonical_label_url(sid) if sid else "",
        api_url=approved_spl_url(sid) if pin else "",
        title=title,
        status=status,
        reason_code=reason_code,
        sections=_empty_sections(),
    )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(element: ElementTree.Element, name: str) -> ElementTree.Element | None:
    for child in element:
        if _local(child.tag) == name:
            return child
    return None


def _text_of(element: ElementTree.Element | None) -> str:
    if element is None:
        return ""
    parts = [" ".join(fragment.split()) for fragment in element.itertext()]
    return " ".join(part for part in parts if part).strip()


def _direct_section_text(section: ElementTree.Element) -> str:
    blocks: list[str] = []
    for child in section:
        if _local(child.tag) == "text":
            value = _text_of(child)
            if value:
                blocks.append(value)
    return "\n".join(blocks).strip()


def _section_code(section: ElementTree.Element) -> str:
    code_el = _child(section, "code")
    if code_el is None:
        return ""
    return (code_el.get("code") or "").strip()


def _section_title(section: ElementTree.Element) -> str:
    return _text_of(_child(section, "title"))


def _bounded(text: str) -> tuple[str, bool]:
    if len(text) <= _UNIT_CONTENT_LIMIT:
        return text, False
    return text[: _UNIT_CONTENT_LIMIT - 1] + "…", True


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _collect_section(
    name: SectionName,
    texts: list[str],
    codes: list[str],
) -> DailyMedSection:
    locator = f"section:{name.replace('_', '-')}"
    joined = "\n\n".join(part for part in texts if part).strip()
    if not joined:
        return DailyMedSection(
            name=name,
            status="UNAVAILABLE",
            locator=locator,
            loinc_codes=tuple(dict.fromkeys(codes)),
            content="",
            truncated=False,
            content_hash="",
        )
    content, truncated = _bounded(joined)
    return DailyMedSection(
        name=name,
        status="PRESENT",
        locator=locator,
        loinc_codes=tuple(dict.fromkeys(codes)),
        content=content,
        truncated=truncated,
        content_hash=_hash(content),
    )


def parse_spl(body: bytes, prototype: PrototypeLabel, retrieved_at: datetime) -> DailyMedLabel:
    """Parse an HL7 v3 SPL document. Authenticity failures are ERROR, not RESOLVED."""
    if not body or len(body) > _MAX_BYTES:
        return _failure(prototype, prototype.ingredient_name, "ERROR", "MALFORMED_RESPONSE", retrieved_at)
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return _failure(prototype, prototype.ingredient_name, "ERROR", "MALFORMED_RESPONSE", retrieved_at)

    if _local(root.tag) != "document":
        return _failure(prototype, prototype.ingredient_name, "ERROR", "MALFORMED_RESPONSE", retrieved_at)
    xmlns = root.tag[1:].split("}", 1)[0] if root.tag.startswith("{") else ""
    if xmlns != SPL_NAMESPACE:
        return _failure(prototype, prototype.ingredient_name, "ERROR", "AUTHENTICITY_FAILURE", retrieved_at)

    set_el = _child(root, "setId")
    found_set = (set_el.get("root") if set_el is not None else "") or ""
    if found_set.strip().lower() != prototype.set_id:
        return _failure(prototype, prototype.ingredient_name, "ERROR", "AUTHENTICITY_FAILURE", retrieved_at)

    title = _text_of(_child(root, "title"))
    if prototype.title_token.lower() not in title.lower():
        return _failure(
            prototype,
            prototype.ingredient_name,
            "ERROR",
            "AUTHENTICITY_FAILURE",
            retrieved_at,
            title=title,
        )

    version_el = _child(root, "versionNumber")
    spl_version = (version_el.get("value") if version_el is not None else "") or ""
    effective = _child(root, "effectiveTime")
    raw_date = (effective.get("value") if effective is not None else "") or ""
    published_date = ""
    if re.fullmatch(r"\d{8}", raw_date):
        published_date = f"{raw_date[0:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date):
        published_date = raw_date

    grouped: dict[SectionName, list[str]] = {name: [] for name in _SECTION_LOINC}
    codes_found: dict[SectionName, list[str]] = {name: [] for name in _SECTION_LOINC}

    for section in root.iter():
        if _local(section.tag) != "section":
            continue
        loinc = _section_code(section)
        if loinc in _EXCLUDED_LOINC:
            continue
        text = _direct_section_text(section)
        title_text = _section_title(section)
        for name, codes in _SECTION_LOINC.items():
            matched = loinc in codes
            if name == "renal_considerations" and not matched:
                matched = bool(_RENAL_TITLE.search(title_text))
            if matched and text:
                grouped[name].append(text)
                if loinc:
                    codes_found[name].append(loinc)

    sections = tuple(
        _collect_section(name, grouped[name], codes_found[name]) for name in _SECTION_LOINC
    )
    return DailyMedLabel(
        ingredient_name=prototype.ingredient_name,
        rxcui=prototype.rxcui,
        set_id=prototype.set_id,
        spl_version=spl_version,
        published_date=published_date,
        retrieved_at=retrieved_at,
        canonical_url=canonical_label_url(prototype.set_id),
        api_url=approved_spl_url(prototype.set_id),
        title=title,
        status="RESOLVED",
        reason_code="LABEL_RETRIEVED",
        sections=sections,
    )


def as_evidence_source(label: DailyMedLabel) -> EvidenceSource | None:
    """Project a verified label into the trusted evidence contract. None if unverified."""
    if label.status != "RESOLVED":
        return None
    present = tuple(section for section in label.sections if section.status == "PRESENT")
    if not present:
        return None
    version = label.published_date or label.retrieved_at.date().isoformat()
    edition = f"spl-{label.spl_version}" if label.spl_version else "spl-unknown"
    payload = "|".join(section.content_hash for section in present)
    return EvidenceSource(
        source_id=f"dailymed-{label.ingredient_name}",
        version=version,
        edition=edition,
        label="REAL_CLINICAL_EVIDENCE",
        title=label.title or f"{label.ingredient_name} FDA labeling",
        publisher=DAILYMED_PUBLISHER,
        canonical_location=label.canonical_url,
        locators=tuple(section.locator for section in present),
        content_hash=_hash(payload),
    )


def as_evidence_units(label: DailyMedLabel) -> tuple[EvidenceUnit, ...]:
    source = as_evidence_source(label)
    if source is None:
        return ()
    units: list[EvidenceUnit] = []
    for section in label.sections:
        if section.status != "PRESENT":
            continue
        units.append(
            EvidenceUnit(
                source_id=source.source_id,
                source_version=source.version,
                source_content_hash=source.content_hash,
                unit_id=section.name.replace("_", "-"),
                version=source.version,
                label="REAL_CLINICAL_EVIDENCE",
                locator=section.locator,
                content=section.content,
                content_hash=section.content_hash,
            )
        )
    return tuple(units)


def _stale_copy(label: DailyMedLabel, retrieved_at: datetime) -> DailyMedLabel:
    return DailyMedLabel(
        ingredient_name=label.ingredient_name,
        rxcui=label.rxcui,
        set_id=label.set_id,
        spl_version=label.spl_version,
        published_date=label.published_date,
        retrieved_at=retrieved_at,
        canonical_url=label.canonical_url,
        api_url=label.api_url,
        title=label.title,
        status="STALE",
        reason_code="CACHE_STALE",
        sections=label.sections,
    )


async def retrieve_label(
    ingredient_name: str,
    *,
    now: datetime | None = None,
    cache: dict[str, _CacheEntry] | None = None,
) -> DailyMedLabel:
    """Fetch pinned DailyMed labeling for one prototype ingredient.

    Unknown names are NOT_FOUND. Network and parse failures are ERROR or
    UNAVAILABLE. A missing section is UNAVAILABLE, never an assertion that the
    warning is absent. Cached labels older than 24 hours are refetched; if the
    refresh fails, the prior snapshot is returned as STALE.
    """
    retrieved_at = now or datetime.now(UTC)
    if retrieved_at.tzinfo is None:
        retrieved_at = retrieved_at.replace(tzinfo=UTC)
    key = ingredient_name.strip().lower()
    prototype = _PROTOTYPE.get(key)
    if prototype is None:
        return _failure(None, key, "NOT_FOUND", "PROTOTYPE_INGREDIENT_UNKNOWN", retrieved_at)

    store = _CACHE if cache is None else cache
    cached = store.get(key)
    if cached is not None and retrieved_at - cached.stored_at <= _CACHE_TTL:
        return cached.label

    url = approved_spl_url(prototype.set_id)
    _assert_approved_request_url(url, prototype.set_id)
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_TIMEOUT_SECONDS, connect=_CONNECT_SECONDS),
            trust_env=False,
            follow_redirects=False,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/xml, text/xml"},
        ) as client:
            response = await client.get(url)
    except httpx.TimeoutException:
        if cached is not None and cached.label.status == "RESOLVED":
            return _stale_copy(cached.label, retrieved_at)
        return _failure(prototype, key, "ERROR", "TIMEOUT", retrieved_at)
    except httpx.RequestError:
        if cached is not None and cached.label.status == "RESOLVED":
            return _stale_copy(cached.label, retrieved_at)
        return _failure(prototype, key, "ERROR", "NETWORK_FAILURE", retrieved_at)

    if response.status_code == 404:
        return _failure(prototype, key, "UNAVAILABLE", "HTTP_UNAVAILABLE", retrieved_at)
    if response.status_code != 200:
        if cached is not None and cached.label.status == "RESOLVED":
            return _stale_copy(cached.label, retrieved_at)
        return _failure(prototype, key, "ERROR", "HTTP_ERROR", retrieved_at)
    if len(response.content) > _MAX_BYTES:
        return _failure(prototype, key, "ERROR", "RESPONSE_TOO_LARGE", retrieved_at)

    label = parse_spl(response.content, prototype, retrieved_at)
    if label.status == "RESOLVED":
        store[key] = _CacheEntry(label=label, stored_at=retrieved_at)
    return label


async def retrieve_prototype_labels(
    *,
    now: datetime | None = None,
    cache: dict[str, _CacheEntry] | None = None,
) -> tuple[DailyMedLabel, ...]:
    results = []
    for name in _PROTOTYPE:
        results.append(await retrieve_label(name, now=now, cache=cache))
    return tuple(results)
