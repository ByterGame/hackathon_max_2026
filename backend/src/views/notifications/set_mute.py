"""Mute or unmute bot messages about one visible card or request."""

from typing import Annotated

from fastapi import Body, Depends, HTTPException, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.notifications.service import NotificationError, set_mute as set_subject_mute
from src.gen.notifications.api import set_mute as models


async def set_mute(
    body: Annotated[models.Request, Body()],
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> models.Response200 | Response:
    try:
        value = await set_subject_mute(
            session,
            actor,
            subject_kind=body.subject_kind.value,
            subject_id=body.subject_id,
            is_muted=body.is_muted,
        )
    except NotificationError as error:
        await session.rollback()
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Настройка уже изменилась") from error
    return models.Response200(is_muted=value)
