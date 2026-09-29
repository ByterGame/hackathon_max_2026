"""Keep MAX profile names separate from administrator overrides.

Revision ID: 20260930_005
Revises: 20260929_004
"""

from alembic import op
import sqlalchemy as sa


revision = "20260930_005"
down_revision = "20260929_004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("max_display_name", sa.Text()), schema="identity")
    op.add_column("users", sa.Column("max_username", sa.Text()), schema="identity")
    op.add_column(
        "users",
        sa.Column(
            "full_name_is_manual",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema="identity",
    )
    # Old full_name values have no provenance: preserve all of them rather than
    # risk replacing a name previously entered by an administrator.
    op.execute(
        "UPDATE identity.users SET full_name_is_manual = true "
        "WHERE full_name IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("users", "full_name_is_manual", schema="identity")
    op.drop_column("users", "max_username", schema="identity")
    op.drop_column("users", "max_display_name", schema="identity")
