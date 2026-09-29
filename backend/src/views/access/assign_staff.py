"""POST /access/assign_staff."""

from typing import Annotated

from fastapi import Body, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.access.staff import assign_staff as scenario
from src.gen.access.api import assign_staff as models
from src.views.access._http import call_access


async def assign_staff(
    body: Annotated[models.Request, Body()],
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> models.Response200 | Response:
    result = await call_access(
        scenario(
            session,
            actor,
            company_id=body.company_id,
            phone_number=body.phone_number,
            can_manage_staff=body.can_manage_staff,
            can_manage_residents=body.can_manage_residents,
            can_manage_issues=body.can_manage_issues,
        ),
        session,
    )
    if isinstance(result, Response):
        return result
    row = result
    return models.Response200(id=row.id, status="active")
