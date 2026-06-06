from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentic_jobs.main import app
from agentic_jobs.config import settings
from agentic_jobs.core.enums import TrustVerdict
from agentic_jobs.db.session import Base, get_session
from agentic_jobs.services.discovery.base import DiscoverySummary
from agentic_jobs.services.drafts.generator import DraftResult
from agentic_jobs.services.trust.evaluator import TrustResult
from agentic_jobs.api.v1.drafts import get_draft_generator


settings.slack_bot_token = None
settings.slack_app_level_token = None
settings.environment = "test"

client = TestClient(app)


def test_trust_evaluate_validates_url() -> None:
    # No body, and a url without a scheme, both fail request validation.
    assert client.post("/api/v1/trust/evaluate").status_code == 422
    assert client.post("/api/v1/trust/evaluate", json={"url": "example.com"}).status_code == 422


def test_trust_evaluate_returns_result(monkeypatch) -> None:
    async def fake_evaluate(url: str, domain_root: str) -> TrustResult:
        return TrustResult(
            score=90,
            verdict=TrustVerdict.AUTO_SAFE,
            signals=[{"name": "tls", "detail": "valid"}],
        )

    monkeypatch.setattr("agentic_jobs.api.v1.trust.evaluate", fake_evaluate)
    response = client.post(
        "/api/v1/trust/evaluate",
        json={"url": "https://example.com/jobs/1"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["domain_root"] == "example.com"
    assert data["score"] == 90
    assert data["verdict"] == "auto-safe"
    assert data["signals"] == [{"name": "tls", "detail": "valid"}]


def test_applications_create_requires_body() -> None:
    assert client.post("/api/v1/applications/create").status_code == 422


def test_applications_create_unknown_job_returns_404() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, future=True)

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session
    try:
        response = client.post(
            "/api/v1/applications/create",
            json={"job_id": str(uuid4())},
        )
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_discover_run_endpoint(monkeypatch) -> None:
    class DummyAdapter:
        job_source_type = None
        submission_mode = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aclose(self):
            return None

    async def fake_run_discovery(session, adapters, settings):
        return DiscoverySummary(orgs_crawled=3, jobs_seen=5, jobs_inserted=4, domains_scored=2)

    monkeypatch.setattr("agentic_jobs.api.v1.discover.GreenhouseAdapter", lambda settings: DummyAdapter())
    monkeypatch.setattr("agentic_jobs.api.v1.discover.GithubPositionsAdapter", lambda *args, **kwargs: DummyAdapter())
    monkeypatch.setattr("agentic_jobs.api.v1.discover.run_discovery", fake_run_discovery)

    response = client.post("/api/v1/discover/run")
    assert response.status_code == 200
    assert response.json() == {
        "orgs_crawled": 3,
        "jobs_seen": 5,
        "jobs_inserted": 4,
        "domains_scored": 2,
    }


def test_drafts_create_stub() -> None:
    fake_id = uuid4()

    class DummyGenerator:
        async def generate(self, *args, **kwargs):
            return DraftResult(
                application_id=fake_id,
                human_id="APP-2025-001",
                version="CL v1",
                cover_letter_md="Dear Hiring Manager,\n\nBody\n\nSincerely,\nApoorva",
                artifact_uri="file:///tmp/cl.md",
                payload={},
            )

    app.dependency_overrides[get_draft_generator] = lambda: DummyGenerator()
    response = client.post(
        "/api/v1/drafts/create",
        json={"application_id": str(fake_id), "notes": [], "author": "tester"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["application_id"] == str(fake_id)
    assert data["human_id"] == "APP-2025-001"
    app.dependency_overrides.clear()


def test_drafts_feedback_stub() -> None:
    fake_id = uuid4()

    class DummyGenerator:
        async def generate(self, *args, **kwargs):
            return DraftResult(
                application_id=fake_id,
                human_id="APP-2025-001",
                version="CL v2",
                cover_letter_md="Update",
                artifact_uri="file:///tmp/cl-v2.md",
                payload={},
            )

    app.dependency_overrides[get_draft_generator] = lambda: DummyGenerator()
    response = client.post(
        "/api/v1/drafts/feedback",
        json={"application_id": str(fake_id), "notes": ["More energy"], "author": "tester"},
    )
    assert response.status_code == 200
    assert response.json()["version"] == "CL v2"
    app.dependency_overrides.clear()
