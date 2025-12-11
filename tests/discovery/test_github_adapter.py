from __future__ import annotations

import asyncio

import pytest

from agentic_jobs.services.discovery.github_adapter import GithubPositionsAdapter


def _build_adapter(test_settings, transport, *, source_name: str, slug: str, urls: list[str]):
    import httpx
    from agentic_jobs.services.discovery.rate_limiter import AsyncRateLimiter

    client = httpx.AsyncClient(transport=transport)
    limiter = AsyncRateLimiter(500, 60.0)
    adapter = GithubPositionsAdapter(
        test_settings,
        source_name=source_name,
        slug=slug,
        data_urls=urls,
        client=client,
        rate_limiter=limiter,
    )
    return adapter, client


@pytest.mark.parametrize(
    ("source_name", "slug", "urls"),
    [
        (
            "simplify",
            "simplify",
            [
                "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/main/data/positions.json",
                "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/main/src/data/positions.json",
            ],
        ),
        (
            "newgrad2026",
            "newgrad2026",
            [
                "https://raw.githubusercontent.com/vanshb03/New-Grad-2026/main/data/positions.json",
                "https://raw.githubusercontent.com/vanshb03/New-Grad-2026/main/src/data/positions.json",
            ],
        ),
    ],
)
def test_github_adapter_list_jobs(test_settings, github_transport_factory, source_name, slug, urls) -> None:
    transport = github_transport_factory()
    adapter, client = _build_adapter(test_settings, transport, source_name=source_name, slug=slug, urls=urls)
    try:
        jobs = asyncio.run(adapter.list_jobs(slug))
    finally:
        asyncio.run(adapter.aclose())
        asyncio.run(client.aclose())

    assert jobs
    job = jobs[0]
    assert job.source == source_name
    assert job.title
    assert job.detail_url.startswith("https://")


def test_github_adapter_infers_company_from_url(test_settings, github_transport_factory) -> None:
    import httpx

    overrides = {
        ("GET", "/SimplifyJobs/New-Grad-Positions/main/data/positions.json"): httpx.Response(
            200,
            json=[
                {
                    "title": "Autonomy Engineer",
                    "url": "https://jobs.lever.co/shieldai/12345",
                    "date_posted": "2099-01-01",
                }
            ],
        )
    }
    transport = github_transport_factory(overrides=overrides)
    adapter, client = _build_adapter(
        test_settings,
        transport,
        source_name="simplify",
        slug="simplify",
        urls=["https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/main/data/positions.json"],
    )
    try:
        jobs = asyncio.run(adapter.list_jobs("simplify"))
    finally:
        asyncio.run(adapter.aclose())
        asyncio.run(client.aclose())

    assert jobs
    assert jobs[0].metadata["company"] == "Shieldai"


def test_github_adapter_fetch_detail(test_settings, github_transport_factory) -> None:
    transport = github_transport_factory()
    adapter, client = _build_adapter(
        test_settings,
        transport,
        source_name="simplify",
        slug="simplify",
        urls=[
            "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/main/data/positions.json",
            "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/main/src/data/positions.json",
        ],
    )
    try:
        jobs = asyncio.run(adapter.list_jobs("simplify"))
        detail = asyncio.run(adapter.fetch_job_detail(jobs[0]))
    finally:
        asyncio.run(adapter.aclose())
        asyncio.run(client.aclose())

    assert detail.company_name
    assert "<h1>" in detail.html
    assert "TestCorp platform team" in detail.html


def test_github_adapter_fallback_url(test_settings, github_transport_factory) -> None:
    import httpx

    overrides = {
        ("GET", "/SimplifyJobs/New-Grad-Positions/main/.github/scripts/listings.json"): httpx.Response(404)
    }
    transport = github_transport_factory(overrides=overrides)
    adapter, client = _build_adapter(
        test_settings,
        transport,
        source_name="simplify",
        slug="simplify",
        urls=[
            "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/main/data/positions.json",
            "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/main/src/data/positions.json",
        ],
    )
    try:
        jobs = asyncio.run(adapter.list_jobs("simplify"))
    finally:
        asyncio.run(adapter.aclose())
        asyncio.run(client.aclose())

    assert jobs  # fallback URL succeeded


def test_github_adapter_filters_old_jobs(test_settings, github_transport_factory) -> None:
    import httpx

    overrides = {
        ("GET", "/SimplifyJobs/New-Grad-Positions/main/data/positions.json"): httpx.Response(
            200,
            json=[
                {
                    "company": "OldCorp",
                    "title": "Legacy Role",
                    "url": "https://oldcorp.example/jobs/1",
                    "date_posted": "2020-01-01",
                }
            ],
        )
    }
    transport = github_transport_factory(overrides=overrides)
    adapter, client = _build_adapter(
        test_settings,
        transport,
        source_name="simplify",
        slug="simplify",
        urls=["https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/main/data/positions.json"],
    )
    try:
        jobs = asyncio.run(adapter.list_jobs("simplify"))
    finally:
        asyncio.run(adapter.aclose())
        asyncio.run(client.aclose())

    assert jobs == []


def test_github_adapter_supports_listings_container(test_settings, github_transport_factory) -> None:
    import httpx

    overrides = {
        ("GET", "/SimplifyJobs/New-Grad-Positions/main/.github/scripts/listings.json"): httpx.Response(
            200,
            json={
                "listings": [
                    {
                        "company": "Future Corp",
                        "title": "New Grad SWE",
                        "url": "https://future.example/jobs/42",
                        "date_posted": "2099-01-03",
                    }
                ]
            },
        )
    }

    transport = github_transport_factory(overrides=overrides)
    adapter, client = _build_adapter(
        test_settings,
        transport,
        source_name="simplify",
        slug="simplify",
        urls=[
            "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/main/.github/scripts/listings.json",
            "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/main/src/data/positions.json",
        ],
    )

    try:
        jobs = asyncio.run(adapter.list_jobs("simplify"))
    finally:
        asyncio.run(adapter.aclose())
        asyncio.run(client.aclose())

    assert len(jobs) == 1
