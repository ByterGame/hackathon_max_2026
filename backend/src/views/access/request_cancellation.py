"""POST /access/request_cancellation."""

from typing import Annotated

from fastapi import Body, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.access.requests import request_cancellation as scenario
from src.gen.access.api import request_cancellation as models
from src.views.access._http import call_access


async def request_cancellation(
    body: Annotated[models.Request, Body()],
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> models.Response200 | Response:
    result = await call_access(
        scenario(session, actor, kind=body.request_kind, request_id=body.request_id),
        session,
    )
    if isinstance(result, Response):
        return result
    row = result
    return models.Response200(id=row.id, status=row.status)
