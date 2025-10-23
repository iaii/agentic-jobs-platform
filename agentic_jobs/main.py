from fastapi import FastAPI

from agentic_jobs.api import api_router
from agentic_jobs.config import settings
from agentic_jobs.services.scheduler.cron import shutdown_scheduler, start_scheduler
from agentic_jobs.services.slack.socket import start_socket_mode, stop_socket_mode


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

    @app.on_event("startup")
    async def _start_scheduler() -> None:
        start_scheduler()
        await start_socket_mode()

    @app.on_event("shutdown")
    async def _stop_scheduler() -> None:
        await shutdown_scheduler()
        await stop_socket_mode()

    return app


app = create_app()
