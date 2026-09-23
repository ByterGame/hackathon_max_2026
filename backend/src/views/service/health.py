"""GET /service/health."""

from src.gen.service.api import health as models


async def health() -> models.Response200:
    return models.Response200(status="ok")
