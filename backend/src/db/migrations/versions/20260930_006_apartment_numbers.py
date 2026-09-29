"""Identify apartments by their number within a house.

Revision ID: 20260930_006
Revises: 20260930_005
"""

from alembic import op
import sqlalchemy as sa


revision = "20260930_006"
down_revision = "20260930_005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Prevent a concurrent old application from inserting a conflicting row
    # between the checks and the replacement of the uniqueness constraints.
    op.execute("LOCK TABLE housing.apartments, access.resident_requests IN ACCESS EXCLUSIVE MODE")
    apartment_duplicate = bind.execute(
        sa.text(
            "SELECT house_id, apartment_number "
            "FROM housing.apartments "
            "GROUP BY house_id, apartment_number HAVING count(*) > 1 LIMIT 1"
        )
    ).first()
    if apartment_duplicate is not None:
        raise RuntimeError(
            "Cannot make apartment numbers unique within a house: "
            f"house {apartment_duplicate.house_id}, "
            f"apartment {apartment_duplicate.apartment_number} has multiple records. "
            "Resolve the conflict manually without deleting resident grants."
        )
    request_duplicate = bind.execute(
        sa.text(
            "SELECT applicant_user_id, house_id, submitted_apartment_number "
            "FROM access.resident_requests "
            "WHERE status IN ('open', 'reviewing', 'needs_info') "
            "GROUP BY applicant_user_id, house_id, submitted_apartment_number "
            "HAVING count(*) > 1 LIMIT 1"
        )
    ).first()
    if request_duplicate is not None:
        raise RuntimeError(
            "Cannot make active resident requests unique by apartment number: "
            f"applicant {request_duplicate.applicant_user_id}, "
            f"house {request_duplicate.house_id}, "
            f"apartment {request_duplicate.submitted_apartment_number} "
            "has multiple active requests. Resolve the conflict manually."
        )

    op.drop_constraint(
        "uq_apartment_location", "apartments", schema="housing", type_="unique"
    )
    op.alter_column(
        "apartments", "entrance_number", schema="housing", existing_type=sa.Integer(), nullable=True
    )
    op.drop_constraint(
        op.f("ck_apartments_entrance_number_positive"), "apartments", schema="housing", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_apartments_entrance_number_positive"),
        "apartments",
        "entrance_number IS NULL OR entrance_number > 0",
        schema="housing",
    )
    op.create_unique_constraint(
        "uq_apartment_location", "apartments", ["house_id", "apartment_number"], schema="housing"
    )

    op.drop_index(
        "uq_resident_requests_active_location", table_name="resident_requests", schema="access"
    )
    op.alter_column(
        "resident_requests",
        "submitted_entrance_number",
        schema="access",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.drop_constraint(
        op.f("ck_resident_requests_entrance_number_positive"),
        "resident_requests",
        schema="access",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_resident_requests_entrance_number_positive"),
        "resident_requests",
        "submitted_entrance_number IS NULL OR submitted_entrance_number > 0",
        schema="access",
    )
    op.create_index(
        "uq_resident_requests_active_location",
        "resident_requests",
        ["applicant_user_id", "house_id", "submitted_apartment_number"],
        unique=True,
        schema="access",
        postgresql_where=sa.text("status IN ('open', 'reviewing', 'needs_info')"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    unknown_apartment = bind.execute(
        sa.text(
            "SELECT id FROM housing.apartments WHERE entrance_number IS NULL LIMIT 1"
        )
    ).scalar_one_or_none()
    unknown_request = bind.execute(
        sa.text(
            "SELECT id FROM access.resident_requests "
            "WHERE submitted_entrance_number IS NULL LIMIT 1"
        )
    ).scalar_one_or_none()
    if unknown_apartment is not None or unknown_request is not None:
        raise RuntimeError(
            "Cannot downgrade while apartments or resident requests have an unknown entrance."
        )

    op.drop_index(
        "uq_resident_requests_active_location", table_name="resident_requests", schema="access"
    )
    op.drop_constraint(
        op.f("ck_resident_requests_entrance_number_positive"),
        "resident_requests",
        schema="access",
        type_="check",
    )
    op.alter_column(
        "resident_requests",
        "submitted_entrance_number",
        schema="access",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.create_check_constraint(
        op.f("ck_resident_requests_entrance_number_positive"),
        "resident_requests",
        "submitted_entrance_number > 0",
        schema="access",
    )
    op.create_index(
        "uq_resident_requests_active_location",
        "resident_requests",
        ["applicant_user_id", "house_id", "submitted_entrance_number", "submitted_apartment_number"],
        unique=True,
        schema="access",
        postgresql_where=sa.text("status IN ('open', 'reviewing', 'needs_info')"),
    )

    op.drop_constraint(
        "uq_apartment_location", "apartments", schema="housing", type_="unique"
    )
    op.drop_constraint(
        op.f("ck_apartments_entrance_number_positive"), "apartments", schema="housing", type_="check"
    )
    op.alter_column(
        "apartments", "entrance_number", schema="housing", existing_type=sa.Integer(), nullable=False
    )
    op.create_check_constraint(
        op.f("ck_apartments_entrance_number_positive"),
        "apartments",
        "entrance_number > 0",
        schema="housing",
    )
    op.create_unique_constraint(
        "uq_apartment_location",
        "apartments",
        ["house_id", "entrance_number", "apartment_number"],
        schema="housing",
    )
