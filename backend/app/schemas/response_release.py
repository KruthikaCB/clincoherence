"""Intent metadata and transient wire representation; never delivery assertions."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.schemas.consistency import ConsistencySchema
from app.schemas.evidence import Digest, EvidenceReference, EvidenceSource, Identifier
from app.schemas.verification_audit import StrictAuditSchema
from app.schemas.verification_gate import GatedRecordText

RELEASE_POLICY = "audited-record-release-v1"
REPRESENTATION = "record-response-json-v1"
EVIDENCE_REPRESENTATION = "record-evidence-json-v2"


class ReleaseIntent(StrictAuditSchema):
    id: UUID
    evaluation_event_id: UUID
    release_policy_version: Literal["audited-record-release-v1"]
    representation_version: Literal["record-response-json-v1", "record-evidence-json-v2"]
    body_digest: Digest
    recorded_at: AwareDatetime


class RecordResponse(ConsistencySchema):
    # Transient only. Never serialize this contract into an audit/intent row.
    representation_version: Literal["record-response-json-v1"] = "record-response-json-v1"
    records: tuple[GatedRecordText, ...]


class ReleasedSyntheticEvidence(ConsistencySchema):
    claim_id: UUID
    fact_version_ids: tuple[UUID, ...] = Field(min_length=1, max_length=1)
    label: Literal["SYNTHETIC_FIXTURE_ONLY"] = "SYNTHETIC_FIXTURE_ONLY"
    source: EvidenceSource
    evidence: EvidenceReference
    locator: str = Field(pattern=r"^section:[a-z0-9-]+$")
    rule_id: Identifier
    rule_version: Identifier
    rule_content_hash: Digest
    approval_manifest_id: Identifier
    approval_manifest_version: Identifier
    approval_manifest_hash: Digest
    intended_use: Literal["synthetic-context-testing"]
    integrity_status: Literal["RESOLVED"]
    applicability_status: Literal["RESOLVED"]
    clinical_support_asserted: Literal[False] = False


class EvidenceRecordResponse(ConsistencySchema):
    representation_version: Literal["record-evidence-json-v2"] = "record-evidence-json-v2"
    records: tuple[GatedRecordText, ...] = Field(min_length=1, max_length=32)
    provenance: tuple[ReleasedSyntheticEvidence, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def coverage(self) -> "EvidenceRecordResponse":
        ids = tuple(r.claim_id for r in self.records)
        if len(set(ids)) != len(ids) or ids != tuple(p.claim_id for p in self.provenance):
            raise ValueError("Exact evidence record coverage required")
        return self
