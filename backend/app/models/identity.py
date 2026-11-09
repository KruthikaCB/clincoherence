"""Foundational identities and current consent; not a record authorization API.

Pending profiles have no account requirement. Consent can reference only an
activated profile. Future services must additionally check current account,
practitioner authorization and consent at every protected operation.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("id", "role", name="uq_users_id_role"),
        CheckConstraint("role IN ('patient', 'practitioner')", name="ck_users_role"),
        CheckConstraint(
            "length(trim(identity)) > 0 AND identity = lower(trim(identity))",
            name="ck_users_identity_normalized",
        ),
        CheckConstraint("length(trim(hashed_password)) > 0", name="ck_users_password_present"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    identity: Mapped[str] = mapped_column(String(320), unique=True)
    # Only password hashes belong here; hashing is performed by security utilities.
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PractitionerProfile(Base):
    __tablename__ = "practitioner_profiles"
    __table_args__ = (
        CheckConstraint("account_role = 'practitioner'", name="ck_practitioner_account_role"),
        ForeignKeyConstraint(
            ["account_id", "account_role"],
            ["users.id", "users.role"],
            name="fk_practitioner_account",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(unique=True)
    account_role: Mapped[str] = mapped_column(
        String(16), default="practitioner", server_default="practitioner"
    )
    is_authorized: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    account: Mapped[User] = relationship()


class PatientProfile(Base):
    __tablename__ = "patient_profiles"
    __table_args__ = (
        UniqueConstraint("id", "is_active", name="uq_patient_id_active"),
        CheckConstraint("account_role = 'patient'", name="ck_patient_account_role"),
        ForeignKeyConstraint(
            ["account_id", "account_role"],
            ["users.id", "users.role"],
            name="fk_patient_account",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(NOT is_active AND activated_at IS NULL) OR "
            "(is_active AND account_id IS NOT NULL AND verified_at IS NOT NULL "
            "AND activated_at IS NOT NULL AND activated_at >= verified_at)",
            name="ck_patient_activation",
        ),
        CheckConstraint(
            "account_id IS NOT NULL OR initiated_by_id IS NOT NULL",
            name="ck_patient_onboarding_origin",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    account_id: Mapped[UUID | None] = mapped_column(unique=True)
    account_role: Mapped[str] = mapped_column(
        String(16), default="patient", server_default="patient"
    )
    initiated_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("practitioner_profiles.id", ondelete="RESTRICT")
    )
    is_active: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    account: Mapped[User | None] = relationship()
    initiated_by: Mapped[PractitionerProfile | None] = relationship()
    consents: Mapped[list["PatientPractitionerConsent"]] = relationship(back_populates="patient")


class PatientPractitionerConsent(Base):
    """Current pair-specific consent; future audit events preserve transitions."""

    __tablename__ = "patient_practitioner_consents"
    __table_args__ = (
        UniqueConstraint("patient_id", "practitioner_id", name="uq_consent_pair"),
        ForeignKeyConstraint(
            ["patient_id", "patient_active"],
            ["patient_profiles.id", "patient_profiles.is_active"],
            name="fk_consent_active_patient",
            ondelete="RESTRICT",
        ),
        CheckConstraint("patient_active", name="ck_consent_active_patient"),
        CheckConstraint("status IN ('granted', 'revoked')", name="ck_consent_status"),
        CheckConstraint(
            "(status = 'granted' AND revoked_at IS NULL) OR "
            "(status = 'revoked' AND revoked_at IS NOT NULL AND revoked_at >= granted_at)",
            name="ck_consent_timestamps",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    patient_id: Mapped[UUID] = mapped_column()
    patient_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    practitioner_id: Mapped[UUID] = mapped_column(
        ForeignKey("practitioner_profiles.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(16))  # Explicit: no grant default.
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    patient: Mapped[PatientProfile] = relationship(back_populates="consents")
    practitioner: Mapped[PractitionerProfile] = relationship()
