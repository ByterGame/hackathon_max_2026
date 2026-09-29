"""Сохранённые состояния пошаговых диалогов бота.

Revision ID: 20260929_002
Revises: 20260928_001
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260929_002"
down_revision = "20260928_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_dialogs",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("flow_kind", sa.Text(), nullable=False),
        sa.Column("step", sa.Text(), nullable=False),
        sa.Column(
            "data",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("draft_id", sa.UUID(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["identity.users.id"],
            name=op.f("fk_bot_dialogs_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["system.drafts.id"],
            name=op.f("fk_bot_dialogs_draft_id_drafts"),
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_bot_dialogs")),
        schema="system",
    )


def downgrade() -> None:
    op.drop_table("bot_dialogs", schema="system")
