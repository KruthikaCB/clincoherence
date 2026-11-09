"""Immutable extraction source, proposals and exactly one individual decision."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ExtractionMessage(Base):
    __tablename__ = "extraction_messages"
    __table_args__ = (
        UniqueConstraint(
            "id", "patient_id", "speaker_id", "speaker_role", name="uq_extraction_message_scope"
        ),
        ForeignKeyConstraint(
            ["speaker_id", "speaker_role"],
            ["users.id", "users.role"],
            ondelete="RESTRICT",
            name="fk_extraction_message_speaker",
        ),
        CheckConstraint(
            "speaker_role IN ('patient', 'practitioner')", name="ck_extraction_message_role"
        ),
        CheckConstraint(
            "data_origin IN ('synthetic', 'de_identified')", name="ck_extraction_message_origin"
        ),
        CheckConstraint("length(text) BETWEEN 1 AND 16000", name="ck_extraction_message_length"),
        CheckConstraint("source_sha256 ~ '^[0-9a-f]{64}$'", name="ck_extraction_message_hash"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patient_profiles.id", ondelete="RESTRICT"))
    speaker_id: Mapped[UUID] = mapped_column()
    speaker_role: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(String(16000))
    data_origin: Mapped[str] = mapped_column(String(16))
    source_sha256: Mapped[str] = mapped_column(String(64))
    provider_name: Mapped[str] = mapped_column(String(80))
    provider_version: Mapped[str] = mapped_column(String(80))
    grounding_version: Mapped[str] = mapped_column(String(80))
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class ConfirmationProposal(Base):
    __tablename__ = "confirmation_proposals"
    __table_args__ = (
        UniqueConstraint("message_id", "ordinal", name="uq_confirmation_message_item"),
        UniqueConstraint(
            "id", "patient_id", "speaker_id", "speaker_role", name="uq_confirmation_proposal_scope"
        ),
        ForeignKeyConstraint(
            ["message_id", "patient_id", "speaker_id", "speaker_role"],
            [
                "extraction_messages.id",
                "extraction_messages.patient_id",
                "extraction_messages.speaker_id",
                "extraction_messages.speaker_role",
            ],
            ondelete="RESTRICT",
            name="fk_confirmation_message_scope",
        ),
        CheckConstraint("ordinal >= 0", name="ck_confirmation_ordinal"),
        CheckConstraint(
            "source_start >= 0 AND source_end > source_start "
            "AND source_end - source_start = length(source_quote)",
            name="ck_confirmation_source_span",
        ),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_confirmation_payload"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    message_id: Mapped[UUID] = mapped_column()
    patient_id: Mapped[UUID] = mapped_column(index=True)
    speaker_id: Mapped[UUID] = mapped_column()
    speaker_role: Mapped[str] = mapped_column(String(16))
    ordinal: Mapped[int] = mapped_column()
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_start: Mapped[int] = mapped_column()
    source_end: Mapped[int] = mapped_column()
    source_quote: Mapped[str] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class ConfirmationDecision(Base):
    __tablename__ = "confirmation_decisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["proposal_id", "patient_id", "speaker_id", "speaker_role"],
            [
                "confirmation_proposals.id",
                "confirmation_proposals.patient_id",
                "confirmation_proposals.speaker_id",
                "confirmation_proposals.speaker_role",
            ],
            ondelete="RESTRICT",
            name="fk_confirmation_decision_scope",
        ),
        ForeignKeyConstraint(
            [
                "accepted_version_id",
                "patient_id",
                "speaker_id",
                "speaker_role",
                "accepted_entry_method",
                "decided_by_id",
            ],
            [
                "clinical_fact_versions.id",
                "clinical_fact_versions.patient_id",
                "clinical_fact_versions.author_id",
                "clinical_fact_versions.author_role",
                "clinical_fact_versions.entry_method",
                "clinical_fact_versions.confirmed_by_id",
            ],
            ondelete="RESTRICT",
            name="fk_confirmation_accepted_fact_scope",
        ),
        CheckConstraint("decided_by_id = speaker_id", name="ck_confirmation_original_speaker"),
        CheckConstraint(
            "(status = 'CONFIRMED' AND accepted_version_id IS NOT NULL "
            "AND accepted_entry_method IS NOT NULL AND accepted_entry_method = 'LLM_EXTRACTED') OR "
            "(status = 'REJECTED' AND accepted_version_id IS NULL "
            "AND accepted_entry_method IS NULL)",
            name="ck_confirmation_decision_result",
        ),
    )
    proposal_id: Mapped[UUID] = mapped_column(primary_key=True)
    patient_id: Mapped[UUID] = mapped_column()
    speaker_id: Mapped[UUID] = mapped_column()
    speaker_role: Mapped[str] = mapped_column(String(16))
    decided_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(16))
    accepted_version_id: Mapped[UUID | None] = mapped_column(unique=True)
    accepted_entry_method: Mapped[str | None] = mapped_column(String(16))
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
