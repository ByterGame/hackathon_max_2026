"""POST /access/create_resident_offer."""

from typing import Annotated

from fastapi import Body, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.access.resident import create_resident_offer as scenario
from src.gen.access.api import create_resident_offer as models
from src.views.access._http import call_access


async def create_resident_offer(
    body: Annotated[models.Request, Body()],
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> models.Response201 | Response:
    result = await call_access(
        scenario(
            session,
            actor,
            house_id=body.house_id,
            entrance_number=body.entrance_number,
            apartment_number=body.apartment_number,
            phone_number=body.phone_number,
            valid_to=body.valid_to,
        ),
        session,
    )
    if isinstance(result, Response):
        return result
    row = result
    return models.Response201(id=row.id, status=row.status)
