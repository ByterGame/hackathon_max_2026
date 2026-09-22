"""GET /example_codegen/client/get_request. Реализацию дополняем вручную."""

from typing import Annotated

from fastapi import HTTPException, Query, Response

from src.gen.example_codegen.api.client import get_request as models


async def get_request(
    query: Annotated[models.QueryParams, Query()],
) -> models.Response200 | models.Response404 | Response:
    raise HTTPException(status_code=501, detail="Not implemented")
