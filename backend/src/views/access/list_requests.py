"""GET /access/list_requests."""

from typing import Annotated

from fastapi import Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.access.queries import list_requests_page as scenario
from src.gen.access.api import list_requests as models
from src.views.access._http import call_access


async def list_requests(
    query: Annotated[models.QueryParams, Query()],
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> models.Response200 | Response:
    result = await call_access(
        scenario(
            session,
            actor,
            kind=query.request_kind,
            house_id=query.house_id,
            company_id=query.company_id,
            limit=query.limit or 100,
            offset=query.offset or 0,
            support_view=query.support_view or False,
        ),
        session,
    )
    if isinstance(result, Response):
        return result
    return models.Response200(**result)
