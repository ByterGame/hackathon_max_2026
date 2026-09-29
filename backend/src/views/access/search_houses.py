"""GET /access/search_houses."""

from typing import Annotated

from fastapi import Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.access.queries import search_houses as scenario
from src.gen.access.api import search_houses as models
from src.views.access._http import call_access


async def search_houses(
    query: Annotated[models.QueryParams, Query()],
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> models.Response200 | Response:
    result = await call_access(scenario(session, query.text), session)
    if isinstance(result, Response):
        return result
    return models.Response200(items=result)
