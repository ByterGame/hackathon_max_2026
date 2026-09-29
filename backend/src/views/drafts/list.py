"""GET /drafts/list."""

from typing import Annotated

from fastapi import Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.drafts.service import list_drafts
from src.gen.drafts.api import list as models
from src.views.drafts._http import call_draft, draft_model


async def list(
    query: Annotated[models.QueryParams, Query()],
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> models.Response200 | Response:
    result = await call_draft(
        list_drafts(session, actor, flow_kind=query.flow_kind), session
    )
    if isinstance(result, Response):
        return result
    return models.Response200(items=[draft_model(item) for item in result])
