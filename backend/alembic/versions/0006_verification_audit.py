"""Append-only metadata-only gate evaluation history.

Revision ID: 0006_verification_audit
Revises: 0005_supervised_extraction
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006_verification_audit"
down_revision = "0005_supervised_extraction"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "verification_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("actor_role", sa.String(16), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("event_kind", sa.String(32), nullable=False),
        sa.Column("audit_schema_version", sa.String(40), nullable=False),
        sa.Column("gate_decision", sa.String(16), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["patient_id"], ["patient_profiles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["actor_id", "actor_role"],
            ["users.id", "users.role"],
            ondelete="RESTRICT",
            name="fk_verification_audit_actor",
        ),
        sa.UniqueConstraint(
            "patient_id",
            "actor_id",
            "operation_id",
            "event_kind",
            name="uq_verification_audit_operation",
        ),
        sa.CheckConstraint(
            "actor_role IN ('patient', 'practitioner')", name="ck_verification_audit_role"
        ),
        sa.CheckConstraint("event_kind = 'GATE_EVALUATED'", name="ck_verification_audit_kind"),
        sa.CheckConstraint(
            "gate_decision IN ('PASS', 'CLARIFY', 'REFUSE')", name="ck_verification_audit_decision"
        ),
        sa.CheckConstraint(
            "audit_schema_version = 'verification-audit-v1'", name="ck_verification_audit_schema"
        ),
        sa.CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'", name="ck_verification_audit_hash"),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'", name="ck_verification_audit_payload"
        ),
        sa.CheckConstraint(
            "payload @> jsonb_build_object('patient_id', patient_id::text, "
            "'actor_id', actor_id::text, 'actor_role', actor_role, 'event_kind', event_kind, "
            "'decision', gate_decision, 'audit_schema_version', audit_schema_version, "
            "'release_status', 'NOT_ASSERTED', 'http_release_authorized', false, "
            "'unrestricted_text_verified', false)",
            name="ck_verification_audit_scope",
        ),
    )
    op.create_index(
        "ix_verification_audit_actor_patient_time",
        "verification_audit_events",
        ["patient_id", "actor_id", "recorded_at", "id"],
    )
    op.execute(
        sa.text("""
        CREATE FUNCTION prevent_verification_audit_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Verification audit is append-only';
        END;
        $$
    """)
    )
    op.execute(
        sa.text("""
        CREATE TRIGGER verification_audit_append_only
        BEFORE UPDATE OR DELETE ON verification_audit_events
        FOR EACH ROW EXECUTE FUNCTION prevent_verification_audit_mutation()
    """)
    )
    op.execute(
        sa.text("""
        CREATE TRIGGER verification_audit_no_truncate
        BEFORE TRUNCATE ON verification_audit_events
        FOR EACH STATEMENT EXECUTE FUNCTION prevent_verification_audit_mutation()
    """)
    )


def downgrade() -> None:
    # Explicit operator-controlled downgrade only; never run automatically.
    op.drop_table("verification_audit_events")
    op.execute(sa.text("DROP FUNCTION prevent_verification_audit_mutation()"))
