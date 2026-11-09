"""Patient-specific synthetic onboarding; only random-secret digests are stored."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PatientInvitation(Base):
    __tablename__ = "patient_invitations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["patient_id", "issued_by_id"],
            ["patient_profiles.id", "patient_profiles.initiated_by_id"],
            name="fk_invitation_patient_issuer",
            ondelete="RESTRICT",
        ),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="ck_invitation_token_hash"),
        CheckConstraint(
            "verification_hash ~ '^[0-9a-f]{64}$'", name="ck_invitation_verification_hash"
        ),
        CheckConstraint(
            "intended_identity ~ '^[a-z0-9][a-z0-9._+-]{0,63}@synthetic[.]invalid$'",
            name="ck_invitation_synthetic_identity",
        ),
        CheckConstraint("expires_at > created_at", name="ck_invitation_expiry"),
        CheckConstraint(
            "(status = 'pending' AND consumed_at IS NULL AND revoked_at IS NULL) OR "
            "(status = 'consumed' AND consumed_at IS NOT NULL AND revoked_at IS NULL "
            "AND consumed_at >= created_at AND consumed_at < expires_at) OR "
            "(status = 'revoked' AND revoked_at IS NOT NULL AND consumed_at IS NULL "
            "AND revoked_at >= created_at)",
            name="ck_invitation_lifecycle",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    patient_id: Mapped[UUID] = mapped_column(unique=True)
    issued_by_id: Mapped[UUID] = mapped_column()
    intended_identity: Mapped[str] = mapped_column(String(320))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    verification_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
