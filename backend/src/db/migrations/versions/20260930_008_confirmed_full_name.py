"""Distinguish an explicitly confirmed full name from a MAX-imported name.

Revision ID: 20260930_008
Revises: 20260930_007
"""

from alembic import op
import sqlalchemy as sa


revision = "20260930_008"
down_revision = "20260930_007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing values may have come from MAX even when full_name_is_manual is true.
    # Never backfill confirmation without an explicit user action.
    op.add_column(
        "users",
        sa.Column("full_name_confirmed_at", sa.DateTime(timezone=True)),
        schema="identity",
    )


def downgrade() -> None:
    op.drop_column("users", "full_name_confirmed_at", schema="identity")
