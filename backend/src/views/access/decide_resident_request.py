"""POST /access/decide_resident_request."""

from typing import Annotated

from fastapi import Body, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.access.resident import decide_resident_request as scenario
from src.gen.access.api import decide_resident_request as models
from src.views.access._http import call_access


async def decide_resident_request(
    body: Annotated[models.Request, Body()],
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> models.Response200 | Response:
    result = await call_access(
        scenario(
            session,
            actor,
            request_id=body.request_id,
            outcome=body.outcome,
            decision_note=body.decision_note,
            valid_to=body.valid_to,
        ),
        session,
    )
    if isinstance(result, Response):
        return result
    row, grant = result
    return models.Response200(
        id=row.id, status=row.status, related_id=grant.id if grant else None
    )
