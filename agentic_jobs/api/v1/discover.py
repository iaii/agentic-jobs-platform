from contextlib import AsyncExitStack
from dataclasses import asdict

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from agentic_jobs.config import settings
from agentic_jobs.db.session import get_session
from agentic_jobs.services.discovery.config import get_job_filter_config
from agentic_jobs.services.discovery.greenhouse_adapter import GreenhouseAdapter
from agentic_jobs.services.discovery.github_adapter import GithubPositionsAdapter
from agentic_jobs.services.discovery.orchestrator import run_discovery
from agentic_jobs.services.discovery.universal.adapter import UniversalAdapter
from agentic_jobs.services.discovery.universal.sites_config import load_universal_sites_config

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
        filter_config = get_job_filter_config(settings.job_filter_config_path)

        if settings.enable_greenhouse and filter_config.adapters.get("greenhouse", True):
            greenhouse = await stack.enter_async_context(GreenhouseAdapter(settings))
            adapters.append(greenhouse)

        if filter_config.adapters.get("simplify", True):
            simplify = await stack.enter_async_context(
                GithubPositionsAdapter(
                    settings,
                    source_name="simplify",
                    slug="simplify",
                    data_urls=settings.simplify_positions_url_list,
                    display_name="GitHub · Simplify",
                )
            )
            adapters.append(simplify)

        if filter_config.adapters.get("newgrad2026", True):
            new_grad = await stack.enter_async_context(
                GithubPositionsAdapter(
                    settings,
                    source_name="newgrad2026",
                    slug="newgrad2026",
                    data_urls=settings.new_grad_positions_url_list,
                    display_name="GitHub · NewGrad2026",
                )
            )
            adapters.append(new_grad)

        if filter_config.adapters.get("universal", True):
            universal_sites = load_universal_sites_config(settings.universal_sites_config_path)
            if universal_sites.feeds:
                universal = await stack.enter_async_context(
                    UniversalAdapter(settings, sites_config=universal_sites)
                )
                adapters.append(universal)

        summary = await run_discovery(db, adapters, settings)

    return asdict(summary)
