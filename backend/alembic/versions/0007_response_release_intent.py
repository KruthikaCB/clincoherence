"""Append-only one-shot release preparation, never delivery history.

Revision ID: 0007_response_release_intent
Revises: 0006_verification_audit
"""

import sqlalchemy as sa

from alembic import op

revision = "0007_response_release_intent"
down_revision = "0006_verification_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "response_release_intents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("evaluation_event_id", sa.Uuid(), nullable=False),
        sa.Column("release_policy_version", sa.String(40), nullable=False),
        sa.Column("representation_version", sa.String(40), nullable=False),
        sa.Column("body_digest", sa.String(64), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["evaluation_event_id"], ["verification_audit_events.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("evaluation_event_id", name="uq_response_release_evaluation"),
        sa.CheckConstraint(
            "release_policy_version = 'audited-record-release-v1'", name="ck_release_policy"
        ),
        sa.CheckConstraint(
            "representation_version = 'record-response-json-v1'", name="ck_release_representation"
        ),
        sa.CheckConstraint("body_digest ~ '^[0-9a-f]{64}$'", name="ck_release_digest"),
    )
    op.execute(
        sa.text("""
        CREATE FUNCTION require_release_pass() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM verification_audit_events
                WHERE id = NEW.evaluation_event_id AND gate_decision = 'PASS') THEN
                RAISE EXCEPTION 'Release requires PASS evaluation' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    )
    op.execute(
        sa.text("""
        CREATE TRIGGER response_release_pass BEFORE INSERT ON response_release_intents
        FOR EACH ROW EXECUTE FUNCTION require_release_pass()
    """)
    )
    op.execute(
        sa.text("""
        CREATE FUNCTION prevent_release_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Release intents are append-only';
        END;
        $$
    """)
    )
    op.execute(
        sa.text("""
        CREATE TRIGGER response_release_append_only
        BEFORE UPDATE OR DELETE ON response_release_intents
        FOR EACH ROW EXECUTE FUNCTION prevent_release_mutation()
    """)
    )
    op.execute(
        sa.text("""
        CREATE TRIGGER response_release_no_truncate BEFORE TRUNCATE ON response_release_intents
        FOR EACH STATEMENT EXECUTE FUNCTION prevent_release_mutation()
    """)
    )


def downgrade() -> None:
    op.drop_table("response_release_intents")
    op.execute(sa.text("DROP FUNCTION prevent_release_mutation()"))
    op.execute(sa.text("DROP FUNCTION require_release_pass()"))
