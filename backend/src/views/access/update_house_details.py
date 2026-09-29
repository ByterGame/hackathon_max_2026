"""POST /access/update_house_details."""

from typing import Annotated

from fastapi import Body, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.access.company import update_house_details as scenario
from src.gen.access.api import update_house_details as models
from src.views.access._http import call_access


async def update_house_details(
    body: Annotated[models.Request, Body()],
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> models.Response200 | Response:
    result = await call_access(
        scenario(
            session,
            actor,
            house_id=body.house_id,
            entrance_count=body.entrance_count,
            apartment_count=body.apartment_count,
        ),
        session,
    )
    if isinstance(result, Response):
        return result
    return models.Response200(
        id=result.id,
        entrance_count=result.entrance_count,
        apartment_count=result.apartment_count,
    )
