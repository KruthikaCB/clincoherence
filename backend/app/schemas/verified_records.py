"""Strict original request envelope; clients cannot supply release authority."""

from uuid import UUID

from app.schemas.consistency import ConsistencySchema
from app.schemas.evidence_aware_draft import EvidenceAwareDraftRequest


class VerifiedRecordsRequest(ConsistencySchema):
    operation_id: UUID
    request: EvidenceAwareDraftRequest
