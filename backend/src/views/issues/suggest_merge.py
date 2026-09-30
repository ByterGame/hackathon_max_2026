"""Find possible duplicate issues for a managing-company employee."""

from typing import Annotated

from fastapi import Body, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.issues.service import IssueError
from src.domain.issues.staff_suggest import suggest_staff_merges
from src.gen.issues.api import suggest_merge as models
from ._presenters import raise_http_issue


async def suggest_merge(
    body: Annotated[models.Request, Body()],
    session: Annotated[AsyncSession, Depends(get_session)],
    actor: Annotated[User, Depends(get_current_user)],
) -> models.Response200 | Response:
    try:
        result = await suggest_staff_merges(session, actor, body.card_id)
    except IssueError as error:
        raise_http_issue(error)
    return models.Response200(
        similar_card_ids=result.similar_card_ids,
        source=result.source,
    )
