"""POST /access/create_house_request."""

from typing import Annotated

from fastapi import Body, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.access.company import create_house_request as scenario
from src.gen.access.api import create_house_request as models
from src.views.access._http import call_access


async def create_house_request(
    body: Annotated[models.Request, Body()],
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> models.Response201 | Response:
    result = await call_access(
        scenario(
            session,
            actor,
            registration_request_id=body.registration_request_id,
            company_id=body.company_id,
            entered_address=body.entered_address,
            entrance_count=body.entrance_count,
            apartment_count=body.apartment_count,
            free_text=body.free_text,
        ),
        session,
    )
    if isinstance(result, Response):
        return result
    row = result
    return models.Response201(id=row.id, status=row.status)
