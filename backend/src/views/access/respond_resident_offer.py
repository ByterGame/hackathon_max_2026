"""POST /access/respond_resident_offer."""

from typing import Annotated

from fastapi import Body, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.access.resident import respond_resident_offer as scenario
from src.gen.access.api import respond_resident_offer as models
from src.views.access._http import call_access


async def respond_resident_offer(
    body: Annotated[models.Request, Body()],
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> models.Response200 | Response:
    result = await call_access(
        scenario(session, actor, offer_id=body.offer_id, accept=body.accept), session
    )
    if isinstance(result, Response):
        return result
    row, grant = result
    return models.Response200(
        id=row.id, status=row.status, related_id=grant.id if grant else None
    )
