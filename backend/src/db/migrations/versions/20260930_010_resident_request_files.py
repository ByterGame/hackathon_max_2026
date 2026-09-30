"""Allow private attachments on resident house-access requests.

Revision ID: 20260930_010
Revises: 20260930_009
"""

from alembic import op
import sqlalchemy as sa


revision = "20260930_010"
down_revision = "20260930_009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "files",
        sa.Column("resident_request_id", sa.UUID(), nullable=True),
        schema="system",
    )
    op.create_foreign_key(
        op.f("fk_files_resident_request_id_resident_requests"),
        "files",
        "resident_requests",
        ["resident_request_id"],
        ["id"],
        source_schema="system",
        referent_schema="access",
    )
    op.drop_constraint(
        "staged_has_only_draft", "files", schema="system", type_="check"
    )
    op.drop_constraint(
        "ready_has_one_parent", "files", schema="system", type_="check"
    )
    op.create_check_constraint(
        "staged_has_only_draft",
        "files",
        "state <> 'staged' OR (draft_id IS NOT NULL AND issue_report_id IS NULL "
        "AND issue_message_id IS NULL AND company_registration_request_id IS NULL "
        "AND house_addition_request_id IS NULL AND resident_request_id IS NULL)",
        schema="system",
    )
    op.create_check_constraint(
        "ready_has_one_parent",
        "files",
        "state <> 'ready' OR (draft_id IS NULL AND "
        "((issue_report_id IS NOT NULL)::integer + (issue_message_id IS NOT NULL)::integer + "
        "(company_registration_request_id IS NOT NULL)::integer + "
        "(house_addition_request_id IS NOT NULL)::integer + "
        "(resident_request_id IS NOT NULL)::integer) = 1)",
        schema="system",
    )


def downgrade() -> None:
    op.drop_constraint(
        "staged_has_only_draft", "files", schema="system", type_="check"
    )
    op.drop_constraint(
        "ready_has_one_parent", "files", schema="system", type_="check"
    )
    op.drop_constraint(
        op.f("fk_files_resident_request_id_resident_requests"),
        "files",
        schema="system",
        type_="foreignkey",
    )
    op.drop_column("files", "resident_request_id", schema="system")
    op.create_check_constraint(
        "staged_has_only_draft",
        "files",
        "state <> 'staged' OR (draft_id IS NOT NULL AND issue_report_id IS NULL "
        "AND issue_message_id IS NULL AND company_registration_request_id IS NULL "
        "AND house_addition_request_id IS NULL)",
        schema="system",
    )
    op.create_check_constraint(
        "ready_has_one_parent",
        "files",
        "state <> 'ready' OR (draft_id IS NULL AND "
        "((issue_report_id IS NOT NULL)::integer + (issue_message_id IS NOT NULL)::integer + "
        "(company_registration_request_id IS NOT NULL)::integer + "
        "(house_addition_request_id IS NOT NULL)::integer) = 1)",
        schema="system",
    )
