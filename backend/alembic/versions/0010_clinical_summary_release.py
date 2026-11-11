"""Add one summary representation; retain all append-only release protections."""

import sqlalchemy as sa

from alembic import op

revision = "0010_clinical_summary_release"
down_revision = "0009_demo_patients"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_release_representation", "response_release_intents", type_="check")
    op.create_check_constraint(
        "ck_release_representation",
        "response_release_intents",
        "representation_version IN ('record-response-json-v1', 'record-evidence-json-v2', "
        "'clinical-summary-json-v1')",
    )


def downgrade() -> None:
    op.execute(
        sa.text("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM response_release_intents
                       WHERE representation_version = 'clinical-summary-json-v1') THEN
                RAISE EXCEPTION 'Cannot downgrade with clinical summary release history';
            END IF;
        END; $$;
    """)
    )
    op.drop_constraint("ck_release_representation", "response_release_intents", type_="check")
    op.create_check_constraint(
        "ck_release_representation",
        "response_release_intents",
        "representation_version IN ('record-response-json-v1', 'record-evidence-json-v2')",
    )
