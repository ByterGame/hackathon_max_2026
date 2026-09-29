"""List issues visible to the signed-in user in one house."""

from typing import Annotated

from fastapi import Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.issues.service import IssueError, list_visible_cards
from src.gen.issues.api import list_cards as models
from ._presenters import card_model, raise_http_issue


async def list_cards(
    query: Annotated[models.QueryParams, Query()],
    session: Annotated[AsyncSession, Depends(get_session)],
    actor: Annotated[User, Depends(get_current_user)],
) -> models.Response200 | Response:
    try:
        cards = await list_visible_cards(
            session, actor, query.house_id, include_closed=bool(query.include_closed)
        )
    except IssueError as error:
        raise_http_issue(error)
    return models.Response200(cards=[await card_model(session, actor, card) for card in cards])
