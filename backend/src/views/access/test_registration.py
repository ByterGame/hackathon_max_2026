"""Test-only company self-approval; the normal support path stays unchanged."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.common.idempotency import reserve_http_command
from src.db.models import User
from src.db.session import get_session
from src.domain.access.test_registration import activate_test_registration
from src.views.access._http import call_access


router = APIRouter(prefix="/access", tags=["access"])


class TestRegistrationRequest(BaseModel):
    request_id: UUID


class TestRegistrationResponse(BaseModel):
    company_id: UUID
    approved_houses: int


@router.post(
    "/test_register_company",
    response_model=TestRegistrationResponse,
    dependencies=[Depends(reserve_http_command)],
)
async def test_register_company(
    body: Annotated[TestRegistrationRequest, Body()],
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TestRegistrationResponse | Response:
    result = await call_access(
        activate_test_registration(session, actor, request_id=body.request_id),
        session,
    )
    if isinstance(result, Response):
        return result
    company, approved_houses = result
    return TestRegistrationResponse(
        company_id=company.id, approved_houses=approved_houses
    )
