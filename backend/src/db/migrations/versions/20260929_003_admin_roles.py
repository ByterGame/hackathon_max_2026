"""Separate administrator role and pending support invitations.

Revision ID: 20260929_003
Revises: 20260929_002
"""

from alembic import op
import sqlalchemy as sa

revision = "20260929_003"
down_revision = "20260929_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_users_user_kind"), "users", schema="identity", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_users_user_kind"),
        "users",
        "kind IN ('unassigned', 'resident', 'employee', 'support', 'admin')",
        schema="identity",
    )
    op.create_table(
        "support_invitations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("phone_number", sa.Text(), nullable=False),
        sa.Column("invited_by", sa.UUID(), nullable=False),
        sa.Column("accepted_by", sa.UUID(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.UUID(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "version", sa.BigInteger(), server_default=sa.text("1"), nullable=False
        ),
        sa.CheckConstraint(
            "phone_number ~ '^7[0-9]{10}$'",
            name=op.f("ck_support_invitations_phone_normalized"),
        ),
        sa.CheckConstraint(
            "(accepted_at IS NULL) = (accepted_by IS NULL)",
            name=op.f("ck_support_invitations_accepted_consistent"),
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL) = (revoked_by IS NULL)",
            name=op.f("ck_support_invitations_revoked_consistent"),
        ),
        sa.CheckConstraint(
            "accepted_at IS NULL OR revoked_at IS NULL",
            name=op.f("ck_support_invitations_one_outcome"),
        ),
        sa.ForeignKeyConstraint(
            ["invited_by"],
            ["identity.users.id"],
            name=op.f("fk_support_invitations_invited_by_users"),
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by"],
            ["identity.users.id"],
            name=op.f("fk_support_invitations_accepted_by_users"),
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"],
            ["identity.users.id"],
            name=op.f("fk_support_invitations_revoked_by_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_support_invitations")),
        schema="identity",
    )
    op.create_index(
        "uq_support_invitations_pending_phone",
        "support_invitations",
        ["phone_number"],
        unique=True,
        schema="identity",
        postgresql_where=sa.text("accepted_at IS NULL AND revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_support_invitations_pending_phone",
        table_name="support_invitations",
        schema="identity",
    )
    op.drop_table("support_invitations", schema="identity")
    op.drop_constraint(
        op.f("ck_users_user_kind"), "users", schema="identity", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_users_user_kind"),
        "users",
        "kind IN ('unassigned', 'resident', 'employee', 'support')",
        schema="identity",
    )
