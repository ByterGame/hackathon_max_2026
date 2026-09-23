"""GET /images/get_next. Реализацию дополняем вручную."""

from typing import Annotated
from urllib.parse import quote

from fastapi import Query

from src.domain.image_pool import choose_next_image, list_images
from src.gen.images.api import get_next as models


async def get_next(
    query: Annotated[models.QueryParams, Query()],
) -> models.Response200 | models.Response404:
    image = choose_next_image(list_images(), query.current_id)
    if image is None:
        return models.Response404(
            code="images_not_found",
            message="В каталоге backend/temp пока нет изображений",
        )
    return models.Response200(id=image, url=f"/media/{quote(image, safe='')}")
