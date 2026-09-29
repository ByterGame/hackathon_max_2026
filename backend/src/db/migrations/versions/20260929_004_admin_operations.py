"""System-editor change journal with authorized corrections.

Revision ID: 20260929_004
Revises: 20260929_003
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260929_004"
down_revision = "20260929_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_operations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("entity_key", sa.Text(), nullable=False),
        sa.Column("row_key", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("expected_etag", sa.Text(), nullable=False),
        sa.Column("before_data", postgresql.JSONB(), nullable=False),
        sa.Column("after_data", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("operation IN ('patch', 'soft_delete', 'hard_delete')", name=op.f("ck_admin_operations_operation_allowed")),
        sa.CheckConstraint("length(btrim(reason)) >= 5", name=op.f("ck_admin_operations_reason_required")),
        sa.ForeignKeyConstraint(["actor_user_id"], ["identity.users.id"], name=op.f("fk_admin_operations_actor_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_operations")),
        schema="system",
    )
    op.create_index(
        "ix_admin_operations_entity_created",
        "admin_operations",
        ["entity_key", "row_key", "created_at"],
        schema="system",
    )
    op.execute(
        """
        CREATE FUNCTION system.guard_admin_operation_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF current_setting('app.system_editor_log_write', true) IS DISTINCT FROM 'on' THEN
                RAISE EXCEPTION 'admin_operations may only be edited by the system editor';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER admin_operations_editor_only
        BEFORE UPDATE OR DELETE ON system.admin_operations
        FOR EACH ROW EXECUTE FUNCTION system.guard_admin_operation_mutation()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION system.block_audit_change() RETURNS trigger
        LANGUAGE plpgsql AS $body$
        BEGIN
            IF current_setting('app.system_editor_log_write', true) IS DISTINCT FROM 'on' THEN
                RAISE EXCEPTION 'audit events may only be edited by the system editor';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $body$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION system.block_audit_change() RETURNS trigger
        LANGUAGE plpgsql AS $body$
        BEGIN
            RAISE EXCEPTION 'audit events are append-only';
        END;
        $body$
        """
    )
    op.execute("DROP TRIGGER admin_operations_editor_only ON system.admin_operations")
    op.execute("DROP FUNCTION system.guard_admin_operation_mutation()")
    op.drop_index("ix_admin_operations_entity_created", table_name="admin_operations", schema="system")
    op.drop_table("admin_operations", schema="system")
