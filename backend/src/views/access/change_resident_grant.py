"""POST /access/change_resident_grant."""

from typing import Annotated

from fastapi import Body, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.access.resident import change_resident_grant as scenario
from src.gen.access.api import change_resident_grant as models
from src.views.access._http import call_access


async def change_resident_grant(
    body: Annotated[models.Request, Body()],
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> models.Response200 | Response:
    result = await call_access(
        scenario(
            session,
            actor,
            grant_id=body.grant_id,
            action=body.action,
            valid_to=body.valid_to,
            reason=body.reason,
        ),
        session,
    )
    if isinstance(result, Response):
        return result
    row = result
    return models.Response200(
        id=row.id, status="revoked" if row.revoked_at else "active"
    )
