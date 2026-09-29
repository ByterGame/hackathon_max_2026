"""List categories available for a new issue."""

from typing import Annotated

from fastapi import Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import IssueCategory
from src.db.session import get_session
from src.gen.issues.api import list_categories as models
from src.gen.issues.internal.issue_data import Category


async def list_categories(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> models.Response200 | Response:
    categories = (
        await session.scalars(
            select(IssueCategory)
            .where(IssueCategory.is_active.is_(True))
            .order_by(IssueCategory.sort_order, IssueCategory.name)
        )
    ).all()
    return models.Response200(
        categories=[Category(id=item.id, code=item.code, name=item.name) for item in categories]
    )
