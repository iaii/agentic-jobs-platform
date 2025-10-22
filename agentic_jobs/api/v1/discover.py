from contextlib import AsyncExitStack
from dataclasses import asdict

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from agentic_jobs.config import settings
from agentic_jobs.db.session import get_session
from agentic_jobs.services.discovery.greenhouse_adapter import GreenhouseAdapter
from agentic_jobs.services.discovery.github_adapter import GithubPositionsAdapter
from agentic_jobs.services.discovery.orchestrator import run_discovery

router = APIRouter()


@router.post(
    "/run",
    status_code=status.HTTP_200_OK,
)
async def run_discovery_endpoint(
    db: Session = Depends(get_session),
) -> dict[str, int]:
    async with AsyncExitStack() as stack:
        adapters = []

        if settings.enable_greenhouse:
            greenhouse = await stack.enter_async_context(GreenhouseAdapter(settings))
            adapters.append(greenhouse)

        simplify = await stack.enter_async_context(
            GithubPositionsAdapter(
                settings,
                source_name="simplify",
                slug="simplify",
                data_urls=settings.simplify_positions_url_list,
            )
        )
        adapters.append(simplify)

        new_grad = await stack.enter_async_context(
            GithubPositionsAdapter(
                settings,
                source_name="newgrad2026",
                slug="newgrad2026",
                data_urls=settings.new_grad_positions_url_list,
            )
        )
        adapters.append(new_grad)

        summary = await run_discovery(db, adapters, settings)

    return asdict(summary)
