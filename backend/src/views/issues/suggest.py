"""Suggest a title and possible duplicates without creating a card."""

from typing import Annotated

from fastapi import Body, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.issues.service import IssueError
from src.domain.issues.suggest import suggest_issue
from src.gen.issues.api import suggest as models
from ._presenters import raise_http_issue


async def suggest(
    body: Annotated[models.Request, Body()],
    session: Annotated[AsyncSession, Depends(get_session)],
    actor: Annotated[User, Depends(get_current_user)],
) -> models.Response200 | Response:
    try:
        result = await suggest_issue(
            session,
            actor,
            house_id=body.house_id,
            description=body.description,
            category_id=body.category_id,
        )
    except IssueError as error:
        raise_http_issue(error)
    return models.Response200(
        suggested_title=result.suggested_title,
        summary_description=result.summary_description,
        similar_card_ids=result.similar_card_ids,
        candidates=[models.Candidate(id=item.id, title=item.title) for item in result.candidates],
        source=result.source,
        description_check=result.description_check,
        description_warning=result.description_warning,
    )
