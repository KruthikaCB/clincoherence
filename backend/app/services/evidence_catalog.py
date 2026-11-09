"""Code-pinned local synthetic catalogue; requests cannot provide trust material."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from app.schemas.consistency import SkippedCheck
from app.schemas.evidence import (
    ApprovalManifest,
    EvidenceBundle,
    RuleDefinition,
    RuleEvaluation,
    RuleRegistry,
    RuleSelection,
    Status,
    VerificationRequest,
)

APPROVAL = "EV-SOURCE-APPROVAL-01"
RESOLUTION = "EV-UNIT-RESOLUTION-01"
CONTEXT = "EV-CONTEXT-APPLICABILITY-01"
ASSETS = Path(__file__).resolve().parents[1] / "fixtures" / "evidence"
# Review/deployment trust anchors. Content and approval remain separate files.
PINS = {
    "bundle.json": "918fe0dc98599dd966550ec00a901c866037be16a68797f1f7517c835b0ac0cc",
    "rules.json": "dfc812ff5191b65bcf48c4f05f4fdd8307c68d97f39027064d0b52e1ee688f55",
    "approvals.json": "c5d6f713467edd37b74ff6d4b1620fb019c7a24142b725a044be6359b25a315f",
}


def canonical_hash(value: object) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def content_hash(model: BaseModel) -> str:
    return canonical_hash(model.model_dump(mode="json", exclude={"content_hash"}))


@dataclass(frozen=True)
class Catalog:
    bundle: EvidenceBundle
    registry: RuleRegistry
    manifest: ApprovalManifest
    manifest_hash: str


@dataclass(frozen=True)
class PreparedRule:
    definition: RuleDefinition | None
    result: RuleEvaluation


@dataclass(frozen=True)
class PreparedEvidence:
    rules: tuple[PreparedRule, ...]
    manifest_hash: str | None
    manifest_id: str | None
    manifest_version: str | None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate catalogue key")
        result[key] = value
    return result


def load_catalog() -> Catalog:
    """Fixed paths only. No network, caller paths, environment override or fallback."""
    assets: dict[str, object] = {}
    for name, pin in PINS.items():
        raw = (ASSETS / name).read_bytes()
        if len(raw) > 64000:
            raise ValueError("Catalogue exceeds local fixture bound")
        obj = json.loads(raw, object_pairs_hook=_unique_object)
        if canonical_hash(obj) != pin:
            raise ValueError("Catalogue trust anchor mismatch")
        assets[name] = obj
    bundle = EvidenceBundle.model_validate(assets["bundle.json"])
    registry = RuleRegistry.model_validate(assets["rules.json"])
    manifest = ApprovalManifest.model_validate(assets["approvals.json"])
    keys: list[list[tuple[str, ...]]] = [
        [(s.source_id, s.version) for s in bundle.sources],
        [(u.source_id, u.source_version, u.unit_id, u.version) for u in bundle.units],
        [(r.rule_id, r.version) for r in registry.rules],
    ]
    if any(len(set(group)) != len(group) for group in keys):
        raise ValueError("Ambiguous catalogue identity")
    return Catalog(bundle, registry, manifest, canonical_hash(assets["approvals.json"]))


def _unavailable(
    selection: RuleSelection,
    reason: str,
    *,
    status: Status = "UNAVAILABLE",
    unit_check: bool = False,
) -> PreparedRule:
    return PreparedRule(
        None,
        RuleEvaluation(
            rule_id=selection.rule_id,
            rule_version=selection.rule_version,
            status=status,
            reason_code=reason,
            checks_run=(APPROVAL, RESOLUTION) if unit_check else (APPROVAL,),
            checks_not_applicable=tuple(
                SkippedCheck(rule_id=r, reason_code="PREREQUISITE_UNAVAILABLE")
                for r in ((CONTEXT,) if unit_check else (RESOLUTION, CONTEXT))
            ),
        ),
    )


def resolve_catalog(request: VerificationRequest, catalog: Catalog) -> PreparedEvidence:
    """Deterministic core for a TRUSTED catalogue. Only prepare_evidence is used
    by the patient service; this function is not a caller approval mechanism.
    """
    prepared = []
    for selection in sorted(request.rules, key=lambda r: (r.rule_id, r.rule_version)):
        prepared.append(_resolve_rule(request, selection, catalog))
    return PreparedEvidence(
        tuple(prepared),
        catalog.manifest_hash,
        catalog.manifest.manifest_id,
        catalog.manifest.version,
    )


def _resolve_rule(
    request: VerificationRequest, selection: RuleSelection, catalog: Catalog
) -> PreparedRule:
    def fail(reason: str) -> PreparedRule:
        return _unavailable(selection, reason)

    manifest = catalog.manifest
    if (
        request.approval_manifest_id != manifest.manifest_id
        or request.approval_manifest_version != manifest.version
    ):
        return fail("MANIFEST_VERSION_MISMATCH")
    rules = [
        r
        for r in catalog.registry.rules
        if (r.rule_id, r.version) == (selection.rule_id, selection.rule_version)
    ]
    if len(rules) != 1:
        return fail("RULE_VERSION_UNAVAILABLE")
    rule = rules[0]
    if content_hash(rule) != rule.content_hash:
        return _unavailable(selection, "RULE_HASH_MISMATCH", status="ERROR")
    if request.intended_use not in rule.intended_uses:
        return fail("INTENDED_USE_NOT_APPROVED")
    if len(selection.evidence) != len(rule.evidence) or set(selection.evidence) != set(
        rule.evidence
    ):
        return fail("EVIDENCE_BINDING_MISMATCH")
    # All units must pass approval BEFORE any evidence material is resolved.
    for ref in rule.evidence:
        approvals = [
            a
            for a in manifest.approvals
            if a.evidence == ref
            and (a.rule_id, a.rule_version, a.rule_content_hash, a.intended_use)
            == (rule.rule_id, rule.version, rule.content_hash, request.intended_use)
        ]
        if len(approvals) != 1:
            return fail("EVIDENCE_NOT_APPROVED")
        if approvals[0].status != "approved":
            return fail("APPROVAL_WITHDRAWN")
        sources = [
            s
            for s in catalog.bundle.sources
            if (s.source_id, s.version) == (ref.source_id, ref.source_version)
        ]
        if len(sources) != 1:
            return fail("SOURCE_UNAVAILABLE")
        source = sources[0]
        if source.edition != ref.source_edition:
            return fail("SOURCE_EDITION_MISMATCH")
        if (
            content_hash(source) != source.content_hash
            or source.content_hash != ref.source_content_hash
        ):
            return fail("SOURCE_HASH_MISMATCH")
    for ref in sorted(
        rule.evidence, key=lambda r: (r.source_id, r.source_version, r.unit_id, r.unit_version)
    ):
        sources = [
            s
            for s in catalog.bundle.sources
            if (s.source_id, s.version) == (ref.source_id, ref.source_version)
        ]
        units = [
            u
            for u in catalog.bundle.units
            if (u.source_id, u.source_version, u.unit_id, u.version)
            == (ref.source_id, ref.source_version, ref.unit_id, ref.unit_version)
        ]
        if len(units) != 1:
            return _unavailable(selection, "UNIT_UNAVAILABLE", unit_check=True)
        unit = units[0]
        if unit.locator not in sources[0].locators:
            return _unavailable(selection, "INVALID_LOCATOR", unit_check=True)
        if (
            unit.source_content_hash != ref.source_content_hash
            or content_hash(unit) != unit.content_hash
            or unit.content_hash != ref.unit_content_hash
        ):
            return _unavailable(selection, "UNIT_HASH_MISMATCH", unit_check=True)
    return PreparedRule(
        rule,
        RuleEvaluation(
            rule_id=rule.rule_id,
            rule_version=rule.version,
            status="RESOLVED",
            reason_code="SYNTHETIC_EVIDENCE_RESOLVED",
            evidence=tuple(
                sorted(
                    rule.evidence,
                    key=lambda r: (r.source_id, r.source_version, r.unit_id, r.unit_version),
                )
            ),
            checks_run=(APPROVAL, RESOLUTION),
            checks_not_applicable=(
                SkippedCheck(rule_id=CONTEXT, reason_code="CONTEXT_NOT_YET_EVALUATED"),
            ),
        ),
    )


def prepare_evidence(request: VerificationRequest) -> PreparedEvidence:
    try:
        return resolve_catalog(request, load_catalog())
    except (OSError, ValueError):
        return PreparedEvidence(
            tuple(
                _unavailable(s, "CATALOG_UNAVAILABLE_OR_INVALID", status="ERROR")
                for s in sorted(request.rules, key=lambda r: (r.rule_id, r.rule_version))
            ),
            None,
            None,
            None,
        )
