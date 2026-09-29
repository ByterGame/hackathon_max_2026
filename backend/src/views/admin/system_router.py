"""Administrator-only HTTP interface for the protected system editor."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.auth import get_current_user
from src.common.idempotency import reserve_http_command
from src.db.models import User
from src.db.session import get_session
from src.domain.access.rules import AccessRuleError
from src.domain.admin.system import (
    system_delete,
    system_get,
    system_list,
    system_operations,
    system_patch,
    system_schema,
)


router = APIRouter(prefix="/admin/system", tags=["admin-system"])


class PatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity: str = Field(min_length=3, max_length=100)
    id: str = Field(min_length=1, max_length=100)
    expected_etag: str = Field(min_length=64, max_length=64)
    reason: str = Field(min_length=5, max_length=2000)
    changes: dict[str, Any]


class DeleteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity: str = Field(min_length=3, max_length=100)
    id: str = Field(min_length=1, max_length=100)
    expected_etag: str = Field(min_length=64, max_length=64)
    reason: str = Field(min_length=5, max_length=2000)
    mode: str


async def _fail(session: AsyncSession, error: AccessRuleError) -> None:
    await session.rollback()
    raise HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
    ) from error


@router.get("/schema")
async def schema(
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    try:
        return await system_schema(session, actor)
    except AccessRuleError as error:
        await _fail(session, error)


@router.get("/list")
async def list_rows(
    entity: str,
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    q: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    try:
        return await system_list(session, actor, entity, q=q, limit=limit, offset=offset)
    except AccessRuleError as error:
        await _fail(session, error)


@router.get("/get")
async def get_row(
    entity: str,
    row_id: Annotated[str, Query(alias="id")],
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    try:
        return await system_get(session, actor, entity, row_id)
    except AccessRuleError as error:
        await _fail(session, error)


@router.get("/operations")
async def list_operations(
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    q: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    try:
        return await system_operations(session, actor, q=q, limit=limit, offset=offset)
    except AccessRuleError as error:
        await _fail(session, error)


@router.post("/patch", dependencies=[Depends(reserve_http_command)])
async def patch_row(
    body: PatchBody,
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    try:
        return await system_patch(
            session, actor, body.entity, body.id, body.expected_etag, body.reason, body.changes
        )
    except AccessRuleError as error:
        await _fail(session, error)


@router.post("/delete", dependencies=[Depends(reserve_http_command)])
async def delete_row(
    body: DeleteBody,
    actor: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    try:
        return await system_delete(
            session, actor, body.entity, body.id, body.expected_etag, body.reason, body.mode
        )
    except AccessRuleError as error:
        await _fail(session, error)
