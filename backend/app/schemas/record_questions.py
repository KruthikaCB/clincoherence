"""Narrow question input; no caller-provided verification or release authority."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.consistency import ConsistencySchema


class RecordQuestion(ConsistencySchema):
    operation_id: UUID
    question: str = Field(strict=True, min_length=1, max_length=500)


class QuestionStatus(ConsistencySchema):
    representation_version: Literal["record-question-status-v1"] = "record-question-status-v1"
    status: Literal["CLARIFY", "REFUSE"]
    reason_code: Literal[
        "QUESTION_UNSUPPORTED",
        "CLINICAL_DECISION_UNSUPPORTED",
        "NO_MATCHING_RECORDS",
        "LATEST_AMBIGUOUS",
        "TOO_MANY_RECORDS",
    ]
