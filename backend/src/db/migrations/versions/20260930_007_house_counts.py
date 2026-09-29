"""Store the building counts supplied by the management company.

Revision ID: 20260930_007
Revises: 20260930_006
"""

from alembic import op
import sqlalchemy as sa


revision = "20260930_007"
down_revision = "20260930_006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("house_addition_requests", sa.Column("entrance_count", sa.Integer()), schema="access")
    op.add_column("house_addition_requests", sa.Column("apartment_count", sa.Integer()), schema="access")
    op.create_check_constraint(
        "entrance_count_positive", "house_addition_requests",
        "entrance_count IS NULL OR entrance_count > 0", schema="access",
    )
    op.create_check_constraint(
        "apartment_count_positive", "house_addition_requests",
        "apartment_count IS NULL OR apartment_count > 0", schema="access",
    )
    op.add_column("houses", sa.Column("apartment_count", sa.Integer()), schema="housing")
    op.create_check_constraint(
        "apartment_count_positive", "houses",
        "apartment_count IS NULL OR apartment_count > 0", schema="housing",
    )


def downgrade() -> None:
    op.drop_constraint("apartment_count_positive", "houses", schema="housing", type_="check")
    op.drop_column("houses", "apartment_count", schema="housing")
    op.drop_constraint("apartment_count_positive", "house_addition_requests", schema="access", type_="check")
    op.drop_constraint("entrance_count_positive", "house_addition_requests", schema="access", type_="check")
    op.drop_column("house_addition_requests", "apartment_count", schema="access")
    op.drop_column("house_addition_requests", "entrance_count", schema="access")
