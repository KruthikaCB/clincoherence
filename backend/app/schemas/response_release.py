"""Intent metadata and transient wire representation; never delivery assertions."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime

from app.schemas.consistency import ConsistencySchema
from app.schemas.evidence import Digest
from app.schemas.verification_audit import StrictAuditSchema
from app.schemas.verification_gate import GatedRecordText

RELEASE_POLICY = "audited-record-release-v1"
REPRESENTATION = "record-response-json-v1"


class ReleaseIntent(StrictAuditSchema):
    id: UUID
    evaluation_event_id: UUID
    release_policy_version: Literal["audited-record-release-v1"]
    representation_version: Literal["record-response-json-v1"]
    body_digest: Digest
    recorded_at: AwareDatetime


class RecordResponse(ConsistencySchema):
    # Transient only. Never serialize this contract into an audit/intent row.
    representation_version: Literal["record-response-json-v1"] = "record-response-json-v1"
    records: tuple[GatedRecordText, ...]
