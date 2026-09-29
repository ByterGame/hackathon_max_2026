"""Create an issue and its first report."""

from typing import Annotated

from fastapi import Body, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.issues.service import IssueError, create_card as create_issue_card
from src.gen.issues.api import create_card as models
from ._presenters import card_model, raise_http_issue


async def create_card(
    body: Annotated[models.Request, Body()],
    session: Annotated[AsyncSession, Depends(get_session)],
    actor: Annotated[User, Depends(get_current_user)],
) -> models.Response201 | Response:
    try:
        card = await create_issue_card(
            session,
            actor,
            house_id=body.house_id,
            category_id=body.category_id,
            title=body.title,
            description=body.description,
            scope_all_house=body.scope_all_house,
            target_entrances=body.target_entrances or [],
            target_apartments=[
                (item.entrance_number, item.apartment_number)
                for item in body.target_apartments or []
            ],
        )
        result = models.Response201(card=await card_model(session, actor, card))
        await session.commit()
    except IssueError as error:
        raise_http_issue(error)
    return result
