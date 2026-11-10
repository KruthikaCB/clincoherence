"""Explicit creator-owned synthetic profiles; ordinary rows remain unchanged."""

import sqlalchemy as sa

from alembic import op

revision = "0009_demo_patients"
down_revision = "0008_evidence_release"
branch_labels = None
depends_on = None

NORMAL = (
    "(NOT is_active AND activated_at IS NULL) OR "
    "(is_active AND account_id IS NOT NULL AND verified_at IS NOT NULL "
    "AND activated_at IS NOT NULL AND activated_at >= verified_at)"
)


def upgrade() -> None:
    op.add_column(
        "patient_profiles",
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("patient_profiles", sa.Column("demo_owner_id", sa.Uuid(), nullable=True))
    op.add_column("patient_profiles", sa.Column("demo_identifier", sa.String(64), nullable=True))
    op.create_foreign_key(
        "fk_patient_demo_owner",
        "patient_profiles",
        "practitioner_profiles",
        ["demo_owner_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_patient_demo_identifier", "patient_profiles", ["demo_owner_id", "demo_identifier"]
    )
    op.drop_constraint("ck_patient_activation", "patient_profiles", type_="check")
    op.create_check_constraint(
        "ck_patient_activation",
        "patient_profiles",
        f"(NOT is_demo AND ({NORMAL})) OR (is_demo AND is_active AND account_id IS NULL "
        "AND verified_at IS NULL AND activated_at IS NULL)",
    )
    op.create_check_constraint(
        "ck_patient_demo_identity",
        "patient_profiles",
        "(NOT is_demo AND demo_owner_id IS NULL AND demo_identifier IS NULL) OR "
        "(is_demo AND demo_owner_id IS NOT NULL AND demo_identifier IS NOT NULL "
        "AND initiated_by_id IS NOT NULL AND initiated_by_id = demo_owner_id "
        "AND demo_identifier ~ '^[a-z0-9][a-z0-9._-]{0,63}$')",
    )
    op.execute(
        sa.text("""
        CREATE FUNCTION prevent_demo_patient_reassignment() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.is_demo IS DISTINCT FROM OLD.is_demo
               OR NEW.demo_owner_id IS DISTINCT FROM OLD.demo_owner_id
               OR NEW.demo_identifier IS DISTINCT FROM OLD.demo_identifier THEN
                RAISE EXCEPTION 'Demo patient identity and ownership are immutable';
            END IF;
            RETURN NEW;
        END; $$
    """)
    )
    op.execute(
        sa.text("""
        CREATE TRIGGER patient_demo_immutable BEFORE UPDATE ON patient_profiles
        FOR EACH ROW EXECUTE FUNCTION prevent_demo_patient_reassignment()
    """)
    )


def downgrade() -> None:
    op.execute(
        sa.text("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM patient_profiles WHERE is_demo) THEN
                RAISE EXCEPTION 'Cannot downgrade with demo patient history';
            END IF;
        END; $$
    """)
    )
    op.execute(sa.text("DROP TRIGGER patient_demo_immutable ON patient_profiles"))
    op.execute(sa.text("DROP FUNCTION prevent_demo_patient_reassignment()"))
    op.drop_constraint("ck_patient_activation", "patient_profiles", type_="check")
    op.create_check_constraint("ck_patient_activation", "patient_profiles", NORMAL)
    op.drop_constraint("ck_patient_demo_identity", "patient_profiles", type_="check")
    op.drop_constraint("uq_patient_demo_identifier", "patient_profiles", type_="unique")
    op.drop_constraint("fk_patient_demo_owner", "patient_profiles", type_="foreignkey")
    for column in ("demo_identifier", "demo_owner_id", "is_demo"):
        op.drop_column("patient_profiles", column)
