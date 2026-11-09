"""Append-only consent snapshots; current access still uses the existing pair row."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ConsentHistory(Base):
    __tablename__ = "consent_history"
    __table_args__ = (
        UniqueConstraint("consent_id", "version", name="uq_consent_history_version"),
        CheckConstraint("version > 0", name="ck_consent_history_version"),
        CheckConstraint(
            "(kind = 'baseline' AND actor_id IS NULL) OR "
            "(kind = 'granted' AND status = 'granted' AND actor_id IS NOT NULL) OR "
            "(kind = 'revoked' AND status = 'revoked' AND actor_id IS NOT NULL)",
            name="ck_consent_history_kind_actor",
        ),
        CheckConstraint(
            "(status = 'granted' AND revoked_at IS NULL) OR "
            "(status = 'revoked' AND revoked_at IS NOT NULL AND revoked_at >= granted_at)",
            name="ck_consent_history_timestamps",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    consent_id: Mapped[UUID] = mapped_column(
        ForeignKey("patient_practitioner_consents.id", ondelete="RESTRICT")
    )
    version: Mapped[int] = mapped_column()
    # Baseline snapshots preserve legacy state without inventing its author.
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
