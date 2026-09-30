import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from .core.config import load_database_config
from .core.logging import safe_exception_message
from .db.session import create_database_engine, create_session_factory
from .http_logging import HTTPLoggingMiddleware
from .views import router
from .views.admin import router as admin_router
from .views.admin import system_router
from .views.access.test_registration import router as test_registration_router
from .views.files import router as files_router
from .views.service.config import router as service_config_router

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MINIAPP_DIST = REPOSITORY_ROOT / "miniapp" / "dist"
logger = logging.getLogger(__name__)


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    phase = "create_database_engine"
    engine = None
    session_factory_created = False
    try:
        engine = create_database_engine(load_database_config())
        phase = "create_session_factory"
        app.state.db_session_factory = create_session_factory(engine)
        session_factory_created = True
        phase = "running"
        logger.info(
            "http_process_started",
            extra={"event": "http_process_started", "phase": phase},
        )
        yield
    except Exception as error:
        logger.error(
            "http_process_failed",
            exc_info=True,
            extra={
                "event": "http_process_failed",
                "phase": phase,
                "result": "failed",
                "exception_type": type(error).__name__,
                "exception_message": safe_exception_message(error),
            },
        )
        raise
    finally:
        if session_factory_created:
            del app.state.db_session_factory
        if engine is not None:
            await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Hackathon MAX API", lifespan=app_lifespan)
    app.add_middleware(HTTPLoggingMiddleware)

    @app.exception_handler(HTTPException)
    async def record_http_exception(request: Request, error: HTTPException):
        if error.status_code >= 500:
            request.state.http_exception = error
        return await http_exception_handler(request, error)

    app.include_router(router)
    app.include_router(admin_router)
    app.include_router(system_router)
    app.include_router(files_router)
    app.include_router(test_registration_router)
    app.include_router(service_config_router)

    if MINIAPP_DIST.is_dir():
        app.mount("/", StaticFiles(directory=MINIAPP_DIST, html=True), name="miniapp")
    return app


app = create_app()
