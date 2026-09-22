from fastapi import FastAPI

from .views import router


def create_app() -> FastAPI:
    app = FastAPI(title="Hackathon MAX API")
    app.include_router(router)
    return app


app = create_app()
