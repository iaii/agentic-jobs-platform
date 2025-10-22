from fastapi.testclient import TestClient

from agentic_jobs.main import app
from agentic_jobs.services.discovery.base import DiscoverySummary


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
    response = client.post("/api/v1/drafts/create")
    assert response.status_code == 200
    assert response.json() == {"message": "stub"}


def test_drafts_feedback_stub() -> None:
    response = client.post("/api/v1/drafts/feedback")
    assert response.status_code == 200
    assert response.json() == {"message": "stub"}
