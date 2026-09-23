from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .domain.image_pool import IMAGE_DIRECTORY
from .views import router


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MINIAPP_DIST = REPOSITORY_ROOT / "miniapp" / "dist"


def create_app() -> FastAPI:
    app = FastAPI(title="Hackathon MAX API")
    app.include_router(router)

    app.mount("/media", StaticFiles(directory=IMAGE_DIRECTORY), name="images")
    if MINIAPP_DIST.is_dir():
        app.mount("/", StaticFiles(directory=MINIAPP_DIST, html=True), name="miniapp")
    return app


app = create_app()
