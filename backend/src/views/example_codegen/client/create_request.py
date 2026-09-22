"""POST /example_codegen/client/create_request. Реализацию дополняем вручную."""

from typing import Annotated

from fastapi import Body, HTTPException, Response

from src.gen.example_codegen.api.client import create_request as models


async def create_request(
    body: Annotated[models.Request, Body()],
) -> models.Response201 | Response:
    raise HTTPException(status_code=501, detail="Not implemented")
