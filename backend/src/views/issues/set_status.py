"""Set an issue status as a UK employee."""

from typing import Annotated

from fastapi import Body, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.issues.service import IssueError, set_status as set_issue_status
from src.gen.issues.api import set_status as models
from ._presenters import card_model, raise_http_issue


async def set_status(
    body: Annotated[models.Request, Body()],
    session: Annotated[AsyncSession, Depends(get_session)],
    actor: Annotated[User, Depends(get_current_user)],
) -> models.Response200 | Response:
    try:
        card = await set_issue_status(
            session,
            actor,
            body.card_id,
            status=body.status.value,
            note=body.note,
            close_result=body.close_result.value if body.close_result else None,
        )
        result = models.Response200(card=await card_model(session, actor, card))
        await session.commit()
    except IssueError as error:
        raise_http_issue(error)
    return result
