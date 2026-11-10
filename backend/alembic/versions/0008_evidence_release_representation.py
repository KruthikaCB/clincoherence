"""Allow the additional digest-bound synthetic evidence response, preserving all rows."""

import sqlalchemy as sa

from alembic import op

revision = "0008_evidence_release"
down_revision = "0007_response_release_intent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_release_representation", "response_release_intents", type_="check")
    op.create_check_constraint(
        "ck_release_representation",
        "response_release_intents",
        "representation_version IN ('record-response-json-v1', 'record-evidence-json-v2')",
    )


def downgrade() -> None:
    # Never rewrite/delete append-only history to make a downgrade fit.
    op.execute(
        sa.text("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM response_release_intents
                       WHERE representation_version = 'record-evidence-json-v2') THEN
                RAISE EXCEPTION 'Cannot downgrade with v2 release history';
            END IF;
        END; $$;
    """)
    )
    op.drop_constraint("ck_release_representation", "response_release_intents", type_="check")
    op.create_check_constraint(
        "ck_release_representation",
        "response_release_intents",
        "representation_version = 'record-response-json-v1'",
    )
