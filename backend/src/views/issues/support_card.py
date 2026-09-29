"""Support a visible issue and optionally add a report."""

from typing import Annotated

from fastapi import Body, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.issues.service import IssueError, support_card as support_issue_card
from src.gen.issues.api import support_card as models
from ._presenters import card_model, raise_http_issue


async def support_card(
    body: Annotated[models.Request, Body()],
    session: Annotated[AsyncSession, Depends(get_session)],
    actor: Annotated[User, Depends(get_current_user)],
) -> models.Response200 | Response:
    try:
        card, report_id = await support_issue_card(session, actor, body.card_id, body.description)
        result = models.Response200(card=await card_model(session, actor, card), report_id=report_id)
        await session.commit()
    except IssueError as error:
        raise_http_issue(error)
    return result
