"""Read the actor's bot mute setting for one visible subject."""

from typing import Annotated

from fastapi import Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.notifications.service import can_view_subject, is_muted
from src.domain.issues.service import resolve_card
from src.gen.notifications.api import get_mute as models


async def get_mute(
    query: Annotated[models.QueryParams, Query()],
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> models.Response200 | Response:
    if not await can_view_subject(session, actor, query.subject_kind.value, query.subject_id):
        raise HTTPException(status_code=404, detail="Карточка или заявка не найдена")
    subject_id = (
        (await resolve_card(session, query.subject_id)).id
        if query.subject_kind.value == "issue_card"
        else query.subject_id
    )
    return models.Response200(
        is_muted=await is_muted(session, actor.id, query.subject_kind.value, subject_id)
    )
