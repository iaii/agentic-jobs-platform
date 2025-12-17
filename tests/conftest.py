import asyncio
import sys
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

from agentic_jobs.config import Settings
from agentic_jobs.db.session import Base
from agentic_jobs.services.discovery.github_adapter import GithubPositionsAdapter
from agentic_jobs.services.discovery.rate_limiter import AsyncRateLimiter
from agentic_jobs.services.discovery.greenhouse_adapter import GreenhouseAdapter

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(element, compiler, **kw):
    return "JSON"


@pytest.fixture
def load_fixture() -> Callable[[str], str]:
    def _loader(filename: str) -> str:
        return (FIXTURES_DIR / filename).read_text(encoding="utf-8")

    return _loader


@pytest.fixture
def test_settings(tmp_path) -> Settings:
    return Settings(
        DATABASE_URL=f"sqlite+pysqlite:///{(tmp_path / 'discovery.db').as_posix()}",
        ENVIRONMENT="test",
        DEBUG=False,
        DISCOVERY_BASE_URL="https://boards.greenhouse.io",
        DISCOVERY_SITEMAP_URL="https://boards.greenhouse.io/sitemap.xml",
        DISCOVERY_INTERVAL_HOURS=3,
        MAX_ORGS_PER_RUN=2,
        REQUESTS_PER_MINUTE=120,
        REQUEST_TIMEOUT_SECONDS=5,
        ALLOWED_DOMAINS="boards.greenhouse.io",
        SIMPLIFY_POSITIONS_URLS="https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/data/positions.json",
        NEW_GRAD_2026_URLS="https://raw.githubusercontent.com/vanshb03/New-Grad-2026/dev/data/positions.json",
    )


@pytest.fixture
def sqlite_session(tmp_path) -> Session:
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'sqlite.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(engine)

    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
    session = TestingSession()

    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def mock_transport_factory(load_fixture: Callable[[str], str]):
    def _factory(overrides: Optional[dict[str, httpx.Response]] = None) -> httpx.MockTransport:
        sitemap = load_fixture("gh_sitemap.xml")
        board_json = load_fixture("gh_board_json.json")
        board_html = load_fixture("gh_board_html.html")
        job_detail_engineer = load_fixture("gh_job_detail_engineer.html")
        job_detail_fullstack = load_fixture("gh_job_detail_fullstack.html")

        async def handler(request: httpx.Request) -> httpx.Response:
            key = (request.method, request.url.path)
            if overrides and key in overrides:
                return overrides[key]

            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nAllow: /\n")
            if request.url.path == "/sitemap.xml":
                return httpx.Response(200, text=sitemap, headers={"Content-Type": "application/xml"})
            if request.url.path == "/testorg/embed/job_board/json":
                return httpx.Response(
                    200,
                    text=board_json,
                    headers={"Content-Type": "application/json"},
                )
            if request.url.path == "/example-startup/embed/job_board/json":
                return httpx.Response(404)
            if request.url.path == "/example-startup":
                return httpx.Response(200, text=board_html, headers={"Content-Type": "text/html"})
            if request.url.path == "/testorg":
                return httpx.Response(200, text=board_html, headers={"Content-Type": "text/html"})
            if request.url.path == "/testorg/jobs/12345":
                return httpx.Response(200, text=job_detail_engineer, headers={"Content-Type": "text/html"})
            if request.url.path == "/testorg/jobs/67890":
                return httpx.Response(200, text=job_detail_fullstack, headers={"Content-Type": "text/html"})
            return httpx.Response(404)

        return httpx.MockTransport(handler)

    return _factory


@pytest.fixture
def github_transport_factory(load_fixture: Callable[[str], str]):
    def _factory(overrides: Optional[dict[str, httpx.Response]] = None) -> httpx.MockTransport:
        simplify = load_fixture("simplify_positions.json")
        new_grad = load_fixture("new_grad_positions.json")

        async def handler(request: httpx.Request) -> httpx.Response:
            key = (request.method, request.url.path)
            if overrides and key in overrides:
                return overrides[key]

            if "SimplifyJobs" in request.url.path:
                return httpx.Response(200, text=simplify, headers={"Content-Type": "application/json"})
            if "vanshb03" in request.url.path:
                return httpx.Response(200, text=new_grad, headers={"Content-Type": "application/json"})

            return httpx.Response(404)

        return httpx.MockTransport(handler)

    return _factory


@pytest.fixture
def greenhouse_adapter(
    test_settings: Settings, mock_transport_factory
) -> GreenhouseAdapter:
    transport = mock_transport_factory()
    client = httpx.AsyncClient(transport=transport)
    limiter = AsyncRateLimiter(500, 60.0)

    adapter = GreenhouseAdapter(test_settings, client=client, rate_limiter=limiter)
    try:
        yield adapter
    finally:
        asyncio.run(adapter.aclose())
        asyncio.run(client.aclose())


@pytest.fixture
def github_adapters(
    test_settings: Settings, github_transport_factory
):
    transport = github_transport_factory()
    client = httpx.AsyncClient(transport=transport)
    limiter = AsyncRateLimiter(500, 60.0)

    simplify_adapter = GithubPositionsAdapter(
        test_settings,
        source_name="simplify",
        slug="simplify",
        data_urls=test_settings.simplify_positions_url_list,
        client=client,
        rate_limiter=limiter,
    )

    new_grad_adapter = GithubPositionsAdapter(
        test_settings,
        source_name="newgrad2026",
        slug="newgrad2026",
        data_urls=test_settings.new_grad_positions_url_list,
        client=client,
        rate_limiter=limiter,
    )

    adapters = [simplify_adapter, new_grad_adapter]

    try:
        yield adapters
    finally:
        for adapter in adapters:
            asyncio.run(adapter.aclose())
        asyncio.run(client.aclose())
