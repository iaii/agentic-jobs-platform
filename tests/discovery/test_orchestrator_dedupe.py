from datetime import datetime, timezone

from agentic_jobs.core.enums import JobSourceType, SubmissionMode
from agentic_jobs.db import models
from agentic_jobs.services.discovery.orchestrator import (
    _hash_exists,
    _job_exists,
)


def _create_job(session, **overrides):
    defaults = {
        "title": "Software Engineer",
        "company_name": "ExampleCo",
        "location": "Remote",
        "url": "https://example.com/jobs/123",
        "source_type": JobSourceType.COMPANY,
        "source_name": "Example Source",
        "domain_root": "example.com",
        "submission_mode": SubmissionMode.DEEPLINK,
        "jd_text": "Build services.",
        "requirements": [{"type": "bullet", "value": "Python"}],
        "job_id_canonical": "SRC:123",
        "scraped_at": datetime.now(timezone.utc),
        "hash": "hash-123",
    }
    defaults.update(overrides)
    job = models.Job(**defaults)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_job_exists_matches_canonical_id(sqlite_session):
    job = _create_job(sqlite_session, job_id_canonical="SRC:job")

    assert _job_exists(sqlite_session, job.job_id_canonical) is True
    assert _job_exists(sqlite_session, "SRC:never-seen") is False


def test_hash_exists_matches_hash(sqlite_session):
    job = _create_job(
        sqlite_session,
        job_id_canonical="SRC:another",
        hash="hash-abc",
    )

    assert _hash_exists(sqlite_session, job.hash) is True
    assert _hash_exists(sqlite_session, "hash-never-seen") is False
