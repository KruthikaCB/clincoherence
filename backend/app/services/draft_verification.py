"""Authenticated read-only adapter; no publication or accepted-state mutations."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.draft_verification import DraftVerificationRequest, DraftVerificationRun
from app.services.authorization import authorize_patient_scope
from app.services.consistency import load_accepted_state
from app.services.consistency_evaluator import ConsistencyInputError
from app.services.draft_verifier import DraftInputError, verify_fact_claims


async def verify_patient_draft(
    db: AsyncSession, account_id: UUID, patient_id: UUID, draft: object
) -> DraftVerificationRun:
    """Caller retains transaction through protected consumption and owns cleanup.

    Future endpoints must use the existing non-streaming release gate. Verification
    records do not authorize later release after dropping locks. No flush/commit,
    evidence operations, external calls or clinical changes occur here.
    """
    current, history = await load_accepted_state(db, account_id, patient_id)
    try:
        # Revalidate dumped data, including preconstructed model_copy inputs;
        # suppress serializer warnings so malformed values cannot reach logs.
        if isinstance(draft, DraftVerificationRequest):
            request = DraftVerificationRequest.model_validate(draft.model_dump(warnings=False))
        else:
            request = DraftVerificationRequest.model_validate(draft)
    except ValidationError:
        raise HTTPException(422, "Invalid draft verification request") from None
    try:
        record = verify_fact_claims(patient_id, current, history, request)
    except (DraftInputError, ConsistencyInputError):
        raise HTTPException(409, "Draft accepted context unavailable") from None
    await authorize_patient_scope(db, account_id, patient_id)
    return DraftVerificationRun(evaluated_at=datetime.now(UTC), verification=record)
