"""Typed administrator API with a fixed list of domain actions."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession
from src.common.auth import get_current_user
from src.common.idempotency import reserve_http_command
from src.db.models import User
from src.db.session import get_session
from src.domain.access.rules import AccessRuleError
from src.domain.admin import admin_action, admin_get, admin_list, admin_overview
from src.domain.admin.service import (
    accept_support_invitation,
    pending_support_invitations,
)

router = APIRouter(prefix="/admin", tags=["admin"])


class ListItem(BaseModel):
    id: UUID
    title: str
    subtitle: str | None = None
    status: str | None = None
    kind: str | None = None
    request_id: UUID | None = None
    created_at: str | None = None
    updated_at: str | None = None
    data: dict[str, Any]


class ListResponse(BaseModel):
    items: list[ListItem]
    total: int
    limit: int
    offset: int


class DetailResponse(BaseModel):
    item: dict[str, Any]


class OverviewResponse(BaseModel):
    counts: dict[str, int]


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any]


class ActionResponse(BaseModel):
    id: UUID
    entity: str
    action: str


class SupportInviteItem(BaseModel):
    id: UUID
    phone_number: str
    created_at: str


class SupportInvitesResponse(BaseModel):
    items: list[SupportInviteItem]


class AcceptSupportInviteRequest(BaseModel):
    invitation_id: UUID


class AcceptedSupportInviteResponse(BaseModel):
    id: UUID
    kind: str


async def _fail(session: AsyncSession, error: AccessRuleError) -> None:
    await session.rollback()
    raise HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
    ) from error


@router.get("/overview", response_model=OverviewResponse)
async def overview(
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OverviewResponse:
    try:
        return OverviewResponse.model_validate(await admin_overview(session, actor))
    except AccessRuleError as error:
        await _fail(session, error)


@router.get("/list", response_model=ListResponse)
async def list_entities(
    entity: str,
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    q: str | None = None,
    limit: int = 20,
    offset: int = 0,
    kind: str | None = None,
) -> ListResponse:
    try:
        result = await admin_list(
            session, actor, entity, q=q, limit=limit, offset=offset, kind=kind
        )
        return ListResponse.model_validate(result)
    except AccessRuleError as error:
        await _fail(session, error)


@router.get("/get", response_model=DetailResponse)
async def get_entity(
    entity: str,
    row_id: Annotated[UUID, Query(alias="id")],
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DetailResponse:
    try:
        return DetailResponse(item=await admin_get(session, actor, entity, row_id))
    except AccessRuleError as error:
        await _fail(session, error)


@router.post(
    "/action",
    response_model=ActionResponse,
    dependencies=[Depends(reserve_http_command)],
)
async def perform_action(
    body: ActionRequest,
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ActionResponse:
    try:
        result = await admin_action(session, actor, body.action, body.payload)
        return ActionResponse.model_validate(result)
    except AccessRuleError as error:
        await _fail(session, error)


@router.get("/support-invites/mine", response_model=SupportInvitesResponse)
async def my_support_invitations(
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SupportInvitesResponse:
    try:
        rows = await pending_support_invitations(session, actor)
        return SupportInvitesResponse(
            items=[
                SupportInviteItem(
                    id=row.id,
                    phone_number=row.phone_number,
                    created_at=row.created_at.isoformat(),
                )
                for row in rows
            ]
        )
    except AccessRuleError as error:
        await _fail(session, error)


@router.post(
    "/support-invites/accept",
    response_model=AcceptedSupportInviteResponse,
    dependencies=[Depends(reserve_http_command)],
)
async def accept_my_support_invitation(
    body: AcceptSupportInviteRequest,
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AcceptedSupportInviteResponse:
    try:
        user = await accept_support_invitation(
            session, actor, invitation_id=body.invitation_id
        )
        return AcceptedSupportInviteResponse(id=user.id, kind=user.kind)
    except AccessRuleError as error:
        await _fail(session, error)
