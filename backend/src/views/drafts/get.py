"""GET /drafts/get."""

from typing import Annotated

from fastapi import Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.drafts.service import get_draft
from src.gen.drafts.api import get as models
from src.views.drafts._http import call_draft, draft_model


async def get(
    query: Annotated[models.QueryParams, Query()],
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> models.Response200 | Response:
    result = await call_draft(get_draft(session, actor, query.draft_id), session)
    if isinstance(result, Response):
        return result
    return models.Response200(draft=draft_model(result))
