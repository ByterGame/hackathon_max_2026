from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles

from .core.config import load_database_config
from .db.session import create_database_engine, create_session_factory
from .views import router
from .views.files import router as files_router


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MINIAPP_DIST = REPOSITORY_ROOT / "miniapp" / "dist"
LEGACY_PATH_PREFIXES = ("/images/", "/example_codegen/")


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    engine = create_database_engine(load_database_config())
    app.state.db_session_factory = create_session_factory(engine)
    try:
        yield
    finally:
        del app.state.db_session_factory
        await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Hackathon MAX API", lifespan=app_lifespan)
    active_routes = [
        route
        for route in router.routes
        if not route.path.startswith(LEGACY_PATH_PREFIXES)
    ]
    app.include_router(APIRouter(routes=active_routes))
    app.include_router(files_router)

    if MINIAPP_DIST.is_dir():
        app.mount("/", StaticFiles(directory=MINIAPP_DIST, html=True), name="miniapp")
    return app


app = create_app()
