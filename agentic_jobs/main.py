from fastapi import FastAPI

import logging
from pathlib import Path

from agentic_jobs.api import api_router
from agentic_jobs.config import settings
from agentic_jobs.db.session import SessionLocal
from agentic_jobs.services.scheduler.cron import shutdown_scheduler, start_scheduler
from agentic_jobs.services.slack.socket import start_socket_mode, stop_socket_mode


LOGGER = logging.getLogger(__name__)

# Suppress verbose SQLAlchemy query logs (only show warnings and above)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Agentic Jobs Platform",
        version="0.1.0",
        debug=settings.debug,
    )

    if settings.autofill_api_token:
        LOGGER.info("AUTOFILL_API_TOKEN loaded (%d chars)", len(settings.autofill_api_token))
    else:
        LOGGER.info("AUTOFILL_API_TOKEN not set")

    @app.get("/healthz")
    def health_check() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(api_router)

    @app.on_event("startup")
    async def _start_scheduler() -> None:
        start_scheduler()
        await start_socket_mode()
        await _refresh_vault_embeddings()

    @app.on_event("startup")
    async def _refresh_vault_embeddings() -> None:
        """
        On startup: check if vault embeddings are stale (file hashes changed)
        and re-embed only the modified sections.

        Skips gracefully if:
        - VAULT_PATH is not configured
        - The embedding endpoint (LM Studio) is not running
        - The vault directory doesn't exist

        This runs in the background so it never blocks server startup.
        """
        if not settings.vault_path:
            return
        vault_path = Path(settings.vault_path)
        if not vault_path.exists():
            LOGGER.warning("Vault path does not exist: %s", vault_path)
            return

        import asyncio

        async def _run() -> None:
            from agentic_jobs.services.vault.embedder import VaultEmbedder
            from agentic_jobs.services.vault.parser import VaultParser

            if not await VaultEmbedder.health_check():
                LOGGER.info(
                    "Vault startup refresh skipped — embedding endpoint not reachable. "
                    "Load nomic-embed-text-v1.5 in LM Studio to enable semantic vault search."
                )
                return

            session = SessionLocal()
            try:
                parser = VaultParser(vault_path)
                sections = parser.parse_all()
                embedder = VaultEmbedder(session)
                refreshed = await embedder.refresh_stale(sections)
                if refreshed:
                    LOGGER.info("Vault startup refresh: %d section(s) re-embedded", refreshed)
                else:
                    LOGGER.info("Vault startup refresh: all embeddings up to date (%d sections)", len(sections))
            except Exception:  # noqa: BLE001
                LOGGER.exception("Vault startup refresh failed — vault search may be unavailable")
            finally:
                session.close()

        asyncio.create_task(_run(), name="vault-startup-refresh")

    @app.on_event("shutdown")
    async def _stop_scheduler() -> None:
        await shutdown_scheduler()
        await stop_socket_mode()

    return app


app = create_app()
