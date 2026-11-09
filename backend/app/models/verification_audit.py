"""Append-only metadata about gate evaluation, never response delivery."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class VerificationAuditEvent(Base):
    __tablename__ = "verification_audit_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["actor_id", "actor_role"],
            ["users.id", "users.role"],
            ondelete="RESTRICT",
            name="fk_verification_audit_actor",
        ),
        UniqueConstraint(
            "patient_id",
            "actor_id",
            "operation_id",
            "event_kind",
            name="uq_verification_audit_operation",
        ),
        CheckConstraint(
            "actor_role IN ('patient', 'practitioner')", name="ck_verification_audit_role"
        ),
        CheckConstraint("event_kind = 'GATE_EVALUATED'", name="ck_verification_audit_kind"),
        CheckConstraint(
            "gate_decision IN ('PASS', 'CLARIFY', 'REFUSE')", name="ck_verification_audit_decision"
        ),
        CheckConstraint(
            "audit_schema_version = 'verification-audit-v1'", name="ck_verification_audit_schema"
        ),
        CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'", name="ck_verification_audit_hash"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_verification_audit_payload"),
        CheckConstraint(
            "payload @> jsonb_build_object('patient_id', patient_id::text, "
            "'actor_id', actor_id::text, 'actor_role', actor_role, 'event_kind', event_kind, "
            "'decision', gate_decision, 'audit_schema_version', audit_schema_version, "
            "'release_status', 'NOT_ASSERTED', 'http_release_authorized', false, "
            "'unrestricted_text_verified', false)",
            name="ck_verification_audit_scope",
        ),
        Index(
            "ix_verification_audit_actor_patient_time",
            "patient_id",
            "actor_id",
            "recorded_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patient_profiles.id", ondelete="RESTRICT"))
    actor_id: Mapped[UUID] = mapped_column()
    actor_role: Mapped[str] = mapped_column(String(16))
    operation_id: Mapped[UUID] = mapped_column()
    event_kind: Mapped[str] = mapped_column(String(32))
    audit_schema_version: Mapped[str] = mapped_column(String(40))
    gate_decision: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    payload_hash: Mapped[str] = mapped_column(String(64))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
