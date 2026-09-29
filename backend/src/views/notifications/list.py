"""List notifications still visible under the actor's current rights."""

from typing import Annotated

from fastapi import Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.notifications.service import list_notifications
from src.gen.notifications.api import list as models


async def list(
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> models.Response200 | Response:
    rows = await list_notifications(session, actor)
    return models.Response200(
        items=[
            models.Item(
                id=notification.id,
                subject_kind=notification.subject_kind,
                subject_id=notification.subject_id,
                event_kind=event.event_kind,
                created_at=notification.created_at,
                read_at=notification.read_at,
                bot_state=notification.bot_state,
            )
            for notification, event in rows
        ]
    )
