"""Edit the shared issue summary without changing resident reports."""

from typing import Annotated

from fastapi import Body, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.issues.service import IssueError, edit_card as edit_issue_card
from src.gen.issues.api import edit_card as models
from ._presenters import card_model, raise_http_issue


async def edit_card(
    body: Annotated[models.Request, Body()],
    session: Annotated[AsyncSession, Depends(get_session)],
    actor: Annotated[User, Depends(get_current_user)],
) -> models.Response200 | Response:
    try:
        card = await edit_issue_card(
            session,
            actor,
            body.card_id,
            expected_version=body.expected_version,
            category_id=body.category_id,
            title=body.title,
            summary_description=body.summary_description,
            scope_all_house=body.scope_all_house,
            target_entrances=body.target_entrances or [],
            target_apartments=[
                (item.entrance_number, item.apartment_number)
                for item in body.target_apartments or []
            ],
        )
        result = models.Response200(card=await card_model(session, actor, card))
        await session.commit()
    except IssueError as error:
        raise_http_issue(error)
    return result
