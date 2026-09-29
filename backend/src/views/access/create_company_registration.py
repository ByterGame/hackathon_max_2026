"""POST /access/create_company_registration."""

from typing import Annotated

from fastapi import Body, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.db.models import User
from src.db.session import get_session
from src.domain.access.company import create_company_registration as scenario
from src.gen.access.api import create_company_registration as models
from src.views.access._http import call_access


async def create_company_registration(
    body: Annotated[models.Request, Body()],
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> models.Response201 | Response:
    result = await call_access(
        scenario(
            session,
            actor,
            phone_number=body.phone_number,
            free_text=body.free_text,
            proposed_company_name=body.proposed_company_name,
        ),
        session,
    )
    if isinstance(result, Response):
        return result
    row = result
    return models.Response201(id=row.id, status=row.status)
