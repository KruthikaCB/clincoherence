"""Immutable patient-bound fact versions, with reserved extracted-fact lifecycle."""

from datetime import datetime
from typing import Any
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ClinicalFactVersion(Base):
    __tablename__ = "clinical_fact_versions"
    __table_args__ = (
        UniqueConstraint("fact_id", "version", name="uq_clinical_fact_version"),
        UniqueConstraint(
            "id",
            "patient_id",
            "author_id",
            "author_role",
            "entry_method",
            "confirmed_by_id",
            name="uq_clinical_extracted_confirmation",
        ),
        UniqueConstraint(
            "id",
            "fact_id",
            "patient_id",
            "attribution",
            "version",
            name="uq_clinical_fact_parent_identity",
        ),
        ForeignKeyConstraint(
            ["previous_version_id", "fact_id", "patient_id", "attribution", "previous_version"],
            [
                "clinical_fact_versions.id",
                "clinical_fact_versions.fact_id",
                "clinical_fact_versions.patient_id",
                "clinical_fact_versions.attribution",
                "clinical_fact_versions.version",
            ],
            name="fk_clinical_fact_same_patient_chain",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["author_id", "author_role"],
            ["users.id", "users.role"],
            name="fk_clinical_fact_author_role",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(author_role = 'patient' AND attribution = 'patient_reported') OR "
            "(author_role = 'practitioner' AND attribution = 'practitioner_managed')",
            name="ck_clinical_fact_attribution",
        ),
        CheckConstraint(
            "(version = 1 AND fact_id = id AND previous_version_id IS NULL "
            "AND previous_version IS NULL AND correction_reason IS NULL) OR "
            "(version > 1 AND previous_version_id IS NOT NULL AND previous_version IS NOT NULL "
            "AND previous_version > 0 "
            "AND version > previous_version AND correction_reason IS NOT NULL "
            "AND length(trim(correction_reason)) > 0)",
            name="ck_clinical_fact_chain",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'CONFIRMED', 'REJECTED')", name="ck_clinical_fact_status"
        ),
        CheckConstraint(
            "(entry_method = 'STRUCTURED' AND status = 'CONFIRMED' "
            "AND confirmed_by_id IS NULL AND confirmed_at IS NULL) OR "
            "(entry_method = 'LLM_EXTRACTED' AND ("
            "(status IN ('PENDING', 'REJECTED') AND confirmed_by_id IS NULL "
            "AND confirmed_at IS NULL) OR "
            "(status = 'CONFIRMED' AND confirmed_by_id IS NOT NULL "
            "AND confirmed_by_id = author_id AND confirmed_at IS NOT NULL)))",
            name="ck_clinical_fact_acceptance",
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object' AND jsonb_typeof(provenance) = 'object'",
            name="ck_clinical_fact_json_objects",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    fact_id: Mapped[UUID] = mapped_column(index=True)
    patient_id: Mapped[UUID] = mapped_column(
        ForeignKey("patient_profiles.id", ondelete="RESTRICT"), index=True
    )
    version: Mapped[int] = mapped_column()
    previous_version_id: Mapped[UUID | None] = mapped_column()
    previous_version: Mapped[int | None] = mapped_column()
    attribution: Mapped[str] = mapped_column(String(32))
    author_id: Mapped[UUID] = mapped_column()
    author_role: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    correction_reason: Mapped[str | None] = mapped_column(String(2000))
    entry_method: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    confirmed_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
