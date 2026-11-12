"""Separate, immutable clinician decisions; never change patient confirmation."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ClinicalFactReviewEvent(Base):
    __tablename__ = "clinical_fact_review_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["fact_version_id", "fact_id", "patient_id", "fact_status"],
            [
                "clinical_fact_versions.id", "clinical_fact_versions.fact_id",
                "clinical_fact_versions.patient_id", "clinical_fact_versions.status",
            ],
            name="fk_review_accepted_patient_fact", ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "patient_id", "reviewer_id", "operation_id", name="uq_review_operation"
        ),
        UniqueConstraint("fact_version_id", "reviewer_id", "sequence", name="uq_review_sequence"),
        UniqueConstraint(
            "id", "patient_id", "fact_version_id", "reviewer_id", "sequence", "decision",
            name="uq_review_predecessor_identity",
        ),
        ForeignKeyConstraint(
            [
                "previous_review_id", "patient_id", "fact_version_id", "reviewer_id",
                "previous_sequence", "previous_decision",
            ],
            [
                "clinical_fact_review_events.id", "clinical_fact_review_events.patient_id",
                "clinical_fact_review_events.fact_version_id",
                "clinical_fact_review_events.reviewer_id", "clinical_fact_review_events.sequence",
                "clinical_fact_review_events.decision",
            ],
            name="fk_review_predecessor_scope", ondelete="RESTRICT",
        ),
        CheckConstraint("fact_status = 'CONFIRMED'", name="ck_review_accepted"),
        CheckConstraint("decision IN ('APPROVE', 'REJECT', 'WITHDRAW')", name="ck_review_decision"),
        CheckConstraint(
            "(sequence = 1 AND previous_review_id IS NULL AND previous_sequence IS NULL "
            "AND previous_decision IS NULL) OR (sequence > 1 AND previous_review_id IS NOT NULL "
            "AND previous_sequence IS NOT NULL AND previous_sequence = sequence - 1 "
            "AND previous_decision IS NOT NULL)", name="ck_review_chain",
        ),
        CheckConstraint(
            "decision <> 'WITHDRAW' OR (previous_decision IS NOT NULL "
            "AND previous_decision = 'APPROVE')", name="ck_review_withdraw_approval",
        ),
        CheckConstraint(
            "(reason IS NULL AND decision = 'APPROVE') OR "
            "(reason IS NOT NULL AND length(trim(reason)) > 0)", name="ck_review_reason",
        ),
        CheckConstraint("policy_version = 'clinician-fact-review-v1'", name="ck_review_policy"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    patient_id: Mapped[UUID] = mapped_column()
    fact_id: Mapped[UUID] = mapped_column()
    fact_version_id: Mapped[UUID] = mapped_column()
    fact_status: Mapped[str] = mapped_column(String(16))
    reviewer_id: Mapped[UUID] = mapped_column(
        ForeignKey("practitioner_profiles.account_id", ondelete="RESTRICT")
    )
    operation_id: Mapped[UUID] = mapped_column()
    sequence: Mapped[int] = mapped_column()
    previous_review_id: Mapped[UUID | None] = mapped_column()
    previous_sequence: Mapped[int | None] = mapped_column()
    previous_decision: Mapped[str | None] = mapped_column(String(16))
    decision: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(2000))
    policy_version: Mapped[str] = mapped_column(String(40))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
