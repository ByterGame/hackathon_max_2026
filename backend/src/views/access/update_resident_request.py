"""POST /access/update_resident_request."""

from typing import Annotated

from fastapi import Body, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.access.resident import update_resident_request as scenario
from src.gen.access.api import update_resident_request as models
from src.views.access._http import call_access


async def update_resident_request(
    body: Annotated[models.Request, Body()],
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> models.Response200 | Response:
    result = await call_access(
        scenario(
            session,
            actor,
            request_id=body.request_id,
            full_name=body.full_name,
            entrance_number=body.entrance_number,
            apartment_number=body.apartment_number,
        ),
        session,
    )
    if isinstance(result, Response):
        return result
    row = result
    return models.Response200(id=row.id, status=row.status)
