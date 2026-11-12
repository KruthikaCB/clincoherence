"""Add clinician-specific fact review history without changing accepted records.

No backfill, implicit approval, or clinical rule activation.
"""

import sqlalchemy as sa

from alembic import op

revision = "0011_clinical_fact_reviews"
down_revision = "0010_clinical_summary_release"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_fact_review_target", "clinical_fact_versions", ["id", "fact_id", "patient_id", "status"]
    )
    op.create_table(
        "clinical_fact_review_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("fact_id", sa.Uuid(), nullable=False),
        sa.Column("fact_version_id", sa.Uuid(), nullable=False),
        sa.Column("fact_status", sa.String(16), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("previous_review_id", sa.Uuid(), nullable=True),
        sa.Column("previous_sequence", sa.Integer(), nullable=True),
        sa.Column("previous_decision", sa.String(16), nullable=True),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(2000), nullable=True),
        sa.Column("policy_version", sa.String(40), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("clock_timestamp()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["reviewer_id"], ["practitioner_profiles.account_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["fact_version_id", "fact_id", "patient_id", "fact_status"],
            ["clinical_fact_versions.id", "clinical_fact_versions.fact_id",
             "clinical_fact_versions.patient_id", "clinical_fact_versions.status"],
            name="fk_review_accepted_patient_fact", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "patient_id", "reviewer_id", "operation_id", name="uq_review_operation"
        ),
        sa.UniqueConstraint(
            "fact_version_id", "reviewer_id", "sequence", name="uq_review_sequence"
        ),
        sa.UniqueConstraint(
            "id", "patient_id", "fact_version_id", "reviewer_id", "sequence", "decision",
            name="uq_review_predecessor_identity",
        ),
        sa.ForeignKeyConstraint(
            ["previous_review_id", "patient_id", "fact_version_id", "reviewer_id",
             "previous_sequence", "previous_decision"],
            ["clinical_fact_review_events.id", "clinical_fact_review_events.patient_id",
             "clinical_fact_review_events.fact_version_id",
             "clinical_fact_review_events.reviewer_id",
             "clinical_fact_review_events.sequence", "clinical_fact_review_events.decision"],
            name="fk_review_predecessor_scope", ondelete="RESTRICT",
        ),
        sa.CheckConstraint("fact_status = 'CONFIRMED'", name="ck_review_accepted"),
        sa.CheckConstraint(
            "decision IN ('APPROVE', 'REJECT', 'WITHDRAW')", name="ck_review_decision"
        ),
        sa.CheckConstraint(
            "(sequence = 1 AND previous_review_id IS NULL AND previous_sequence IS NULL "
            "AND previous_decision IS NULL) OR (sequence > 1 AND previous_review_id IS NOT NULL "
            "AND previous_sequence IS NOT NULL AND previous_sequence = sequence - 1 "
            "AND previous_decision IS NOT NULL)", name="ck_review_chain",
        ),
        sa.CheckConstraint(
            "decision <> 'WITHDRAW' OR (previous_decision IS NOT NULL "
            "AND previous_decision = 'APPROVE')", name="ck_review_withdraw_approval",
        ),
        sa.CheckConstraint(
            "(reason IS NULL AND decision = 'APPROVE') OR "
            "(reason IS NOT NULL AND length(trim(reason)) > 0)", name="ck_review_reason",
        ),
        sa.CheckConstraint(
            "policy_version = 'clinician-fact-review-v1'", name="ck_review_policy"
        ),
    )
    op.execute(sa.text("""
        CREATE FUNCTION prevent_clinical_review_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Clinician review history is append-only';
        END;
        $$
    """))
    op.execute(sa.text("""
        CREATE TRIGGER clinical_review_append_only
        BEFORE UPDATE OR DELETE ON clinical_fact_review_events
        FOR EACH ROW EXECUTE FUNCTION prevent_clinical_review_mutation()
    """))
    op.execute(sa.text("""
        CREATE TRIGGER clinical_review_no_truncate
        BEFORE TRUNCATE ON clinical_fact_review_events
        FOR EACH STATEMENT EXECUTE FUNCTION prevent_clinical_review_mutation()
    """))


def downgrade() -> None:
    # Lock before checking emptiness: a concurrent insert cannot be lost.
    op.execute(sa.text("LOCK TABLE clinical_fact_review_events IN ACCESS EXCLUSIVE MODE"))
    op.execute(sa.text("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM clinical_fact_review_events) THEN
                RAISE EXCEPTION 'Cannot downgrade with clinician review history';
            END IF;
        END; $$;
    """))
    op.drop_table("clinical_fact_review_events")
    op.execute(sa.text("DROP FUNCTION prevent_clinical_review_mutation()"))
    op.drop_constraint("uq_fact_review_target", "clinical_fact_versions", type_="unique")
