"""Store the shared issue description separately from resident reports.

Revision ID: 20260930_009
Revises: 20260930_008
"""

from alembic import op
import sqlalchemy as sa


revision = "20260930_009"
down_revision = "20260930_008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cards",
        sa.Column("summary_description", sa.Text(), nullable=True),
        schema="issues",
    )
    op.execute(
        """
        UPDATE issues.cards AS card
        SET summary_description = LEFT(
            COALESCE(
                (
                    SELECT NULLIF(BTRIM(REGEXP_REPLACE(
                        report.raw_description, '[[:space:]]+', ' ', 'g'
                    )), '')
                    FROM issues.reports AS report
                    WHERE report.origin_card_id = card.id
                    ORDER BY report.created_at, report.id
                    LIMIT 1
                ),
                NULLIF(BTRIM(REGEXP_REPLACE(card.title, '[[:space:]]+', ' ', 'g')), ''),
                'Проблема дома'
            ),
            1500
        )
        """
    )
    op.alter_column("cards", "summary_description", nullable=False, schema="issues")
    op.create_check_constraint(
        "summary_description_length",
        "cards",
        "length(summary_description) BETWEEN 1 AND 1500 "
        "AND NULLIF(BTRIM(summary_description), '') IS NOT NULL",
        schema="issues",
    )


def downgrade() -> None:
    op.drop_constraint(
        "summary_description_length",
        "cards",
        schema="issues",
        type_="check",
    )
    op.drop_column("cards", "summary_description", schema="issues")
