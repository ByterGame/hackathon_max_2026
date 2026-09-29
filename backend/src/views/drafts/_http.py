"""Common HTTP presentation for shared drafts."""

from collections.abc import Awaitable
from typing import TypeVar

from fastapi import Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Draft
from src.domain.drafts.service import DraftError
from src.gen.drafts.internal.draft_data import DraftData


T = TypeVar("T")


def draft_model(draft: Draft) -> DraftData:
    return DraftData(
        id=draft.id,
        flow_kind=draft.flow_kind,
        payload=draft.payload,
        revision=draft.revision,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
        submitted_at=draft.submitted_at,
        expires_at=draft.expires_at,
    )


async def call_draft(
    operation: Awaitable[T], session: AsyncSession, *, commit: bool = False
) -> T | Response:
    try:
        result = await operation
        if commit:
            await session.commit()
        return result
    except DraftError as error:
        await session.rollback()
        return JSONResponse(
            status_code=error.status_code,
            content={"code": error.code, "message": error.message},
        )
