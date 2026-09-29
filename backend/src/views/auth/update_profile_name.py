"""Confirm or change the account-wide full name."""

from typing import Annotated

from fastapi import Body, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.profile import update_profile_name as scenario
from src.gen.auth.api import update_profile_name as models
from src.views.access._http import call_access


async def update_profile_name(
    body: Annotated[models.Request, Body()],
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> models.Response200 | Response:
    user = await call_access(
        scenario(session, actor, full_name=body.full_name), session
    )
    if isinstance(user, Response):
        return user
    return models.Response200(full_name=user.full_name, full_name_confirmed=True)
