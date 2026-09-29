"""Add a resident comment or an official UK reply."""

from typing import Annotated

from fastapi import Body, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.issues.service import IssueError, add_comment as add_issue_comment
from src.gen.issues.api import add_comment as models
from ._presenters import message_model, raise_http_issue


async def add_comment(
    body: Annotated[models.Request, Body()],
    session: Annotated[AsyncSession, Depends(get_session)],
    actor: Annotated[User, Depends(get_current_user)],
) -> models.Response201 | Response:
    try:
        message = await add_issue_comment(session, actor, body.card_id, body.text)
        result = models.Response201(message=message_model(message))
        await session.commit()
    except IssueError as error:
        raise_http_issue(error)
    return result
