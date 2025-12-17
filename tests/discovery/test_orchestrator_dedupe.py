from datetime import datetime, timedelta

from agentic_jobs.core.enums import JobSourceType, SubmissionMode
from agentic_jobs.db import models
from agentic_jobs.services.discovery.orchestrator import (
    _hash_seen_recently,
    _job_seen_recently,
)


def _create_job(session, **overrides):
    defaults = {
        "title": "Software Engineer",
        "company_name": "ExampleCo",
        "location": "Remote",
        "url": "https://example.com/jobs/123",
        "source_type": JobSourceType.COMPANY,
        "domain_root": "example.com",
        "submission_mode": SubmissionMode.DEEPLINK,
        "jd_text": "Build services.",
        "requirements": [{"type": "bullet", "value": "Python"}],
        "job_id_canonical": "SRC:123",
        "scraped_at": datetime.utcnow(),
        "hash": "hash-123",
    }
    defaults.update(overrides)
    job = models.Job(**defaults)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_job_seen_recently_respects_cutoff(sqlite_session):
    cutoff = datetime.utcnow() - timedelta(days=30)
    job = _create_job(
        sqlite_session,
        job_id_canonical="SRC:job",
        scraped_at=datetime.utcnow() - timedelta(days=45),
    )

    assert _job_seen_recently(sqlite_session, job.job_id_canonical, cutoff) is False

    job.scraped_at = datetime.utcnow()
    sqlite_session.commit()
    sqlite_session.refresh(job)

    assert _job_seen_recently(sqlite_session, job.job_id_canonical, cutoff) is True


def test_hash_seen_recently_respects_cutoff(sqlite_session):
    cutoff = datetime.utcnow() - timedelta(days=30)
    job = _create_job(
        sqlite_session,
        job_id_canonical="SRC:another",
        hash="hash-abc",
        scraped_at=datetime.utcnow() - timedelta(days=60),
    )

    assert _hash_seen_recently(sqlite_session, job.hash, cutoff) is False

    job.scraped_at = datetime.utcnow()
    sqlite_session.commit()
    sqlite_session.refresh(job)

    assert _hash_seen_recently(sqlite_session, job.hash, cutoff) is True
