"""GET /service/ready: проверка доступа к PostgreSQL."""

import asyncio

from fastapi import Request
from sqlalchemy import text

from src.gen.service.api import ready as models


async def ready(request: Request) -> models.Response200 | models.Response503:
    try:
        factory = request.app.state.db_session_factory
        async with factory() as session:
            result = await asyncio.wait_for(session.scalar(text("SELECT 1")), timeout=3)
    except Exception:
        return models.Response503(status="unavailable")
    if result != 1:
        return models.Response503(status="unavailable")
    return models.Response200(status="ok")
