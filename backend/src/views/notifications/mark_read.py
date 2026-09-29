"""Mark one of the actor's current notifications as read."""

from typing import Annotated

from fastapi import Body, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.notifications.service import NotificationError, mark_read as mark_notification_read
from src.gen.notifications.api import mark_read as models


async def mark_read(
    body: Annotated[models.Request, Body()],
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> models.Response200 | Response:
    try:
        await mark_notification_read(session, actor, body.notification_id)
    except NotificationError as error:
        await session.rollback()
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    return models.Response200(read=True)
