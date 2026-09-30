"""Mark all of the current user's notifications as read."""

from typing import Annotated

from fastapi import Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.notifications.service import mark_all_read as mark_all_notifications_read
from src.gen.notifications.api import mark_all_read as models


async def mark_all_read(
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> models.Response200 | Response:
    marked = await mark_all_notifications_read(session, actor)
    return models.Response200(marked=marked)
