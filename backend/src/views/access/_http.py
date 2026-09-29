"""Преобразование предметных ошибок доступа в ответы API."""

from collections.abc import Awaitable
from typing import TypeVar

from fastapi import Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.access.rules import AccessRuleError

T = TypeVar("T")


async def call_access(operation: Awaitable[T], session: AsyncSession) -> T | Response:
    try:
        return await operation
    except AccessRuleError as error:
        await session.rollback()
        return JSONResponse(
            status_code=error.status_code,
            content={"code": error.code, "message": error.message},
        )
    except IntegrityError:
        await session.rollback()
        return JSONResponse(
            status_code=409,
            content={
                "code": "conflict",
                "message": "Данные изменились или уже существуют",
            },
        )
