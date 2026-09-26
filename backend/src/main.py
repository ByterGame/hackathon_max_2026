from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .core.config import load_database_config
from .db.session import create_database_pool
from .domain.image_pool import IMAGE_DIRECTORY
from .views import router


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MINIAPP_DIST = REPOSITORY_ROOT / "miniapp" / "dist"


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    async with create_database_pool(load_database_config()) as pool:
        app.state.db_pool = pool
        try:
            yield
        finally:
            del app.state.db_pool


def create_app() -> FastAPI:
    app = FastAPI(title="Hackathon MAX API", lifespan=app_lifespan)
    app.include_router(router)

    app.mount("/media", StaticFiles(directory=IMAGE_DIRECTORY), name="images")
    if MINIAPP_DIST.is_dir():
        app.mount("/", StaticFiles(directory=MINIAPP_DIST, html=True), name="miniapp")
    return app


app = create_app()
