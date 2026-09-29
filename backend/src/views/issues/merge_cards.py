"""Merge two active issue cards in the same house."""

from typing import Annotated

from fastapi import Body, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.issues.service import IssueError, merge_cards as merge_issue_cards
from src.gen.issues.api import merge_cards as models
from ._presenters import card_model, raise_http_issue


async def merge_cards(
    body: Annotated[models.Request, Body()],
    session: Annotated[AsyncSession, Depends(get_session)],
    actor: Annotated[User, Depends(get_current_user)],
) -> models.Response200 | Response:
    try:
        card = await merge_issue_cards(
            session,
            actor,
            body.left_id,
            body.right_id,
            final_title=body.final_title,
            final_status=body.final_status.value,
            final_note=body.final_note,
        )
        result = models.Response200(card=await card_model(session, actor, card))
        await session.commit()
    except IssueError as error:
        raise_http_issue(error)
    return result
