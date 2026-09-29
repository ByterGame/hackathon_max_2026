"""List access offers created for houses currently managed by this UK."""

from typing import Annotated

from fastapi import Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.access.queries import list_company_offers as scenario
from src.gen.access.api import list_company_offers as models
from src.views.access._http import call_access


async def list_company_offers(
    query: Annotated[models.QueryParams, Query()],
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> models.Response200 | Response:
    items = await call_access(
        scenario(
            session,
            actor,
            company_id=query.company_id,
            house_id=query.house_id,
        ),
        session,
    )
    if isinstance(items, Response):
        return items
    return models.Response200(items=[models.OfferItem(**item) for item in items])
