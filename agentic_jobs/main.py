from fastapi import FastAPI

from agentic_jobs.api import api_router
from agentic_jobs.config import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title="Agentic Jobs Platform",
        version="0.1.0",
        debug=settings.debug,
    )

    @app.get("/healthz")
    def health_check() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(api_router)
    return app


app = create_app()
