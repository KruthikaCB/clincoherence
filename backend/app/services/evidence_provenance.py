"""Allowlisted projection of independently resolved synthetic HbA1c context only."""

from fastapi import HTTPException

from app.schemas.evidence import VerificationRequest
from app.schemas.response_release import EvidenceRecordResponse, ReleasedSyntheticEvidence
from app.schemas.verification_gate import GateDecisionRecord
from app.services.evidence_aware_evaluator import catalogue_fingerprint
from app.services.evidence_catalog import load_catalog, resolve_catalog


def evidence_response(gate: GateDecisionRecord) -> EvidenceRecordResponse:
    try:
        fingerprint = catalogue_fingerprint()
        if (
            gate.decision != "PASS"
            or gate.verification is None
            or fingerprint is None
            or gate.verification.catalogue_fingerprint != fingerprint
        ):
            raise ValueError()
        catalog = load_catalog()
        output = []
        for claim in gate.claims:
            assessment, grounding = claim.evidence, claim.grounding
            if (
                claim.decision != "PASS"
                or grounding is None
                or grounding.outcome != "GROUNDED"
                or len(grounding.fact_version_ids) != 1
                or assessment is None
                or assessment.applicability is None
            ):
                raise ValueError()
            binding = assessment.binding
            if (
                binding.claim_id != claim.claim_id
                or binding.rule.rule_id
                not in {"synthetic-patient-lab-context", "synthetic-practitioner-lab-context"}
                or binding.rule.rule_version != "fixture-1"
                or binding.intended_use != "synthetic-context-testing"
            ):
                raise ValueError()
            # Resolve again from trust anchors, never project caller approval flags or metadata.
            resolved = resolve_catalog(
                VerificationRequest(
                    patient_id=gate.patient_id,
                    fact_version_ids=grounding.fact_version_ids,
                    rules=(binding.rule,),
                    approval_manifest_id=binding.approval_manifest_id,
                    approval_manifest_version=binding.approval_manifest_version,
                    intended_use=binding.intended_use,
                ),
                catalog,
            )
            trusted = resolved.rules[0]
            definition = trusted.definition
            app = assessment.applicability
            if (
                trusted.result.status != "RESOLVED"
                or trusted.result != assessment.integrity
                or definition is None
                or len(definition.evidence) != 1
                or assessment.approval_manifest_id != catalog.manifest.manifest_id
                or assessment.approval_manifest_version != catalog.manifest.version
                or assessment.approval_manifest_hash != catalog.manifest_hash
            ):
                raise ValueError()
            if (
                app.status != "RESOLVED"
                or app.reason_code != "SYNTHETIC_CONTEXT_MATCHED"
                or app.rule_id != definition.rule_id
                or app.rule_version != definition.version
                or app.evidence != definition.evidence
                or app.matched_fact_version_ids != grounding.fact_version_ids
            ):
                raise ValueError()
            if (
                len(definition.requirements) != 1
                or definition.requirements[0].kind != "laboratory"
                or definition.requirements[0].code != "hba1c"
                or definition.requirements[0].unit != "%"
            ):
                raise ValueError()
            ref = definition.evidence[0]
            source = next(
                s
                for s in catalog.bundle.sources
                if (s.source_id, s.version, s.content_hash, s.edition)
                == (ref.source_id, ref.source_version, ref.source_content_hash, ref.source_edition)
            )
            unit = next(
                u
                for u in catalog.bundle.units
                if (
                    u.source_id,
                    u.source_version,
                    u.source_content_hash,
                    u.unit_id,
                    u.version,
                    u.content_hash,
                )
                == (
                    ref.source_id,
                    ref.source_version,
                    ref.source_content_hash,
                    ref.unit_id,
                    ref.unit_version,
                    ref.unit_content_hash,
                )
            )
            output.append(
                ReleasedSyntheticEvidence(
                    claim_id=claim.claim_id,
                    fact_version_ids=grounding.fact_version_ids,
                    source=source,
                    evidence=ref,
                    locator=unit.locator,
                    rule_id=definition.rule_id,
                    rule_version=definition.version,
                    rule_content_hash=definition.content_hash,
                    approval_manifest_id=catalog.manifest.manifest_id,
                    approval_manifest_version=catalog.manifest.version,
                    approval_manifest_hash=catalog.manifest_hash,
                    intended_use="synthetic-context-testing",
                    integrity_status="RESOLVED",
                    applicability_status="RESOLVED",
                )
            )
        if catalogue_fingerprint() != fingerprint:
            raise ValueError()
        return EvidenceRecordResponse(records=gate.rendered_records, provenance=tuple(output))
    except (ValueError, OSError, StopIteration):
        raise HTTPException(503, "Synthetic provenance unavailable") from None
