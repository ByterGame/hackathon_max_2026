"""POST /access/add_discussion_message."""

from typing import Annotated

from fastapi import Body, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.access.requests import add_discussion_message as scenario
from src.gen.access.api import add_discussion_message as models
from src.views.access._http import call_access


async def add_discussion_message(
    body: Annotated[models.Request, Body()],
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> models.Response201 | Response:
    result = await call_access(
        scenario(
            session,
            actor,
            kind=body.request_kind,
            request_id=body.request_id,
            text=body.text,
        ),
        session,
    )
    if isinstance(result, Response):
        return result
    row, message_id = result
    return models.Response201(id=message_id, status=row.status, related_id=row.id)
