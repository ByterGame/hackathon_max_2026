"""Return the current MAX-linked account."""

from typing import Annotated

from fastapi import Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import Company, StaffAssignment as StaffRow, User
from src.db.session import get_session
from src.domain.profile import has_confirmed_full_name
from src.gen.auth.api import me as models


async def me(
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> models.Response200 | Response:
    rows = (
        await session.execute(
            select(StaffRow, Company)
            .join(Company, Company.id == StaffRow.company_id)
            .where(
                StaffRow.user_id == actor.id,
                StaffRow.revoked_at.is_(None),
                Company.archived_at.is_(None),
            )
            .order_by(Company.display_name)
        )
    ).all()
    return models.Response200(
        id=actor.id,
        kind=actor.kind,
        full_name=actor.full_name,
        full_name_confirmed=has_confirmed_full_name(actor),
        phone_number=actor.phone_number,
        phone_verified=actor.phone_verified_at is not None,
        staff_assignments=[
            models.StaffAssignment(
                company_id=company.id,
                company_name=company.display_name,
                can_manage_staff=staff.can_manage_staff,
                can_manage_residents=staff.can_manage_residents,
                can_manage_issues=staff.can_manage_issues,
            )
            for staff, company in rows
        ],
    )
