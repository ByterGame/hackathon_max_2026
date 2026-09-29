"""Let the original reporter reopen a closed issue."""

from typing import Annotated

from fastapi import Body, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.issues.service import IssueError, reopen_card as reopen_issue_card
from src.gen.issues.api import reopen_card as models
from ._presenters import card_model, raise_http_issue


async def reopen_card(
    body: Annotated[models.Request, Body()],
    session: Annotated[AsyncSession, Depends(get_session)],
    actor: Annotated[User, Depends(get_current_user)],
) -> models.Response200 | Response:
    try:
        card = await reopen_issue_card(session, actor, body.card_id, body.comment)
        result = models.Response200(card=await card_model(session, actor, card))
        await session.commit()
    except IssueError as error:
        raise_http_issue(error)
    return result
