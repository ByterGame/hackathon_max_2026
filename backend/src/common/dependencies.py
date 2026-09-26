"""Ресурсы, передаваемые HTTP-обработчикам через FastAPI."""

from collections.abc import AsyncIterator
from typing import Annotated

import asyncpg
from fastapi import Depends, Request


async def get_db_connection(request: Request) -> AsyncIterator[asyncpg.Connection]:
    async with request.app.state.db_pool.acquire() as connection:
        yield connection


DBConnection = Annotated[asyncpg.Connection, Depends(get_db_connection)]
