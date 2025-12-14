from uuid import uuid4

from fastapi.testclient import TestClient

from agentic_jobs.main import app
from agentic_jobs.config import settings
from agentic_jobs.services.discovery.base import DiscoverySummary
from agentic_jobs.services.drafts.generator import DraftResult
from agentic_jobs.api.v1.drafts import get_draft_generator


settings.slack_bot_token = None
settings.slack_app_level_token = None
settings.environment = "test"

client = TestClient(app)


def test_trust_evaluate_stub() -> None:
    response = client.post("/api/v1/trust/evaluate")
    assert response.status_code == 200
    assert response.json() == {"message": "stub"}


def test_applications_create_stub() -> None:
    response = client.post("/api/v1/applications/create")
    assert response.status_code == 200
    assert response.json() == {"message": "stub"}


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
