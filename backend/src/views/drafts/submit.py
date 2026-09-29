"""POST /drafts/submit."""

from typing import Annotated

from fastapi import Body, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.drafts.service import mark_submitted
from src.gen.drafts.api import submit as models
from src.views.drafts._http import call_draft, draft_model


async def submit(
    body: Annotated[models.Request, Body()],
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> models.Response200 | Response:
    result = await call_draft(
        mark_submitted(session, actor, draft_id=body.draft_id, revision=body.revision),
        session,
        commit=True,
    )
    if isinstance(result, Response):
        return result
    return models.Response200(draft=draft_model(result))
