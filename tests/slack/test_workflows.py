from datetime import date, datetime, timedelta

from agentic_jobs.core.enums import JobSourceType, SubmissionMode
from agentic_jobs.db import models
from agentic_jobs.services.slack.workflows import (
    collect_digest_rows,
    last_posted_job_scraped_at,
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
        "job_id_canonical": overrides.get("job_id_canonical", f"SRC:{datetime.utcnow().timestamp()}"),
        "scraped_at": datetime.utcnow(),
        "hash": overrides.get("hash", f"hash-{datetime.utcnow().timestamp()}"),
    }
    defaults.update(overrides)
    job = models.Job(**defaults)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _log_digest(session, job, digest_day):
    log = models.DigestLog(
        job_id=job.id,
        digest_date=digest_day,
        slack_channel_id="C123",
        slack_message_ts="1700.0",
    )
    session.add(log)
    session.commit()
    return log


def test_collect_digest_rows_uses_last_posted_cutoff(sqlite_session):
    older = _create_job(
        sqlite_session,
        job_id_canonical="SRC:old",
        scraped_at=datetime.utcnow() - timedelta(days=5),
    )
    newer = _create_job(
        sqlite_session,
        job_id_canonical="SRC:new",
        scraped_at=datetime.utcnow() - timedelta(days=1),
    )

    _log_digest(sqlite_session, older, digest_day=date(2024, 1, 1))

    rows = collect_digest_rows(
        sqlite_session,
        since=older.scraped_at,
        digest_day=date.today(),
        limit=10,
    )

    assert len(rows) == 1
    assert rows[0].job_id == newer.id


def test_last_posted_job_scraped_at_returns_latest_logged(sqlite_session):
    older = _create_job(
        sqlite_session,
        job_id_canonical="SRC:old2",
        scraped_at=datetime.utcnow() - timedelta(days=10),
    )
    newer = _create_job(
        sqlite_session,
        job_id_canonical="SRC:new2",
        scraped_at=datetime.utcnow() - timedelta(days=2),
    )

    _log_digest(sqlite_session, older, digest_day=date(2024, 1, 2))

    assert last_posted_job_scraped_at(sqlite_session) == older.scraped_at

    _log_digest(sqlite_session, newer, digest_day=date(2024, 1, 3))

    assert last_posted_job_scraped_at(sqlite_session) == newer.scraped_at
