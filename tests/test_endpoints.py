from fastapi.testclient import TestClient

from agentic_jobs.main import app


client = TestClient(app)


def test_trust_evaluate_stub() -> None:
    response = client.post("/api/v1/trust/evaluate")
    assert response.status_code == 200
    assert response.json() == {"message": "stub"}


def test_applications_create_stub() -> None:
    response = client.post("/api/v1/applications/create")
    assert response.status_code == 200
    assert response.json() == {"message": "stub"}


def test_discover_run_stub() -> None:
    response = client.post("/api/v1/discover/run")
    assert response.status_code == 200
    assert response.json() == {"message": "stub"}


def test_drafts_create_stub() -> None:
    response = client.post("/api/v1/drafts/create")
    assert response.status_code == 200
    assert response.json() == {"message": "stub"}


def test_drafts_feedback_stub() -> None:
    response = client.post("/api/v1/drafts/feedback")
    assert response.status_code == 200
    assert response.json() == {"message": "stub"}
