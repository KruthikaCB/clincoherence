"""One-shot prepared intent, not a record of sending or delivery."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ResponseReleaseIntent(Base):
    __tablename__ = "response_release_intents"
    __table_args__ = (
        UniqueConstraint("evaluation_event_id", name="uq_response_release_evaluation"),
        CheckConstraint(
            "release_policy_version = 'audited-record-release-v1'", name="ck_release_policy"
        ),
        CheckConstraint(
            "representation_version = 'record-response-json-v1'", name="ck_release_representation"
        ),
        CheckConstraint("body_digest ~ '^[0-9a-f]{64}$'", name="ck_release_digest"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    evaluation_event_id: Mapped[UUID] = mapped_column(
        ForeignKey("verification_audit_events.id", ondelete="RESTRICT")
    )
    release_policy_version: Mapped[str] = mapped_column(String(40))
    representation_version: Mapped[str] = mapped_column(String(40))
    body_digest: Mapped[str] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
