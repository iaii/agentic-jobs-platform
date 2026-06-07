from datetime import datetime, timezone

import pytest

from agentic_jobs.core.enums import ApplicationStage, JobSourceType, SubmissionMode
from agentic_jobs.db import models
from agentic_jobs.services.applications import human_id as human_id_mod
from agentic_jobs.services.applications.human_id import (
    insert_application_with_human_id,
    next_human_id,
)
from agentic_jobs.services.applications.stage import apply_stage


def _create_job(session, **overrides):
    stamp = datetime.now(timezone.utc).timestamp()
    defaults = {
        "title": "Software Engineer",
        "company_name": "ExampleCo",
        "location": "Remote",
        "url": f"https://example.com/jobs/{overrides.get('job_id_canonical', stamp)}",
        "source_type": JobSourceType.COMPANY,
        "source_name": "Example Source",
        "domain_root": "example.com",
        "submission_mode": SubmissionMode.DEEPLINK,
        "jd_text": "Build services.",
        "requirements": [],
        "job_id_canonical": overrides.get("job_id_canonical", f"SRC:{stamp}"),
        "scraped_at": datetime.now(timezone.utc),
        "hash": overrides.get("hash", f"hash-{stamp}"),
    }
    defaults.update(overrides)
    job = models.Job(**defaults)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _build_for(job):
    def _build(hid: str) -> models.Application:
        app = models.Application(
            human_id=hid,
            job_id=job.id,
            score=0.5,
            canonical_job_id=job.job_id_canonical,
            submission_mode=job.submission_mode,
        )
        apply_stage(app, ApplicationStage.INTERESTED)
        return app
    return _build


def test_allocates_sequential_ids(sqlite_session):
    year = datetime.now(timezone.utc).year
    job1 = _create_job(sqlite_session, job_id_canonical="SRC:a")
    job2 = _create_job(sqlite_session, job_id_canonical="SRC:b")

    app1 = insert_application_with_human_id(sqlite_session, _build_for(job1))
    sqlite_session.commit()
    app2 = insert_application_with_human_id(sqlite_session, _build_for(job2))
    sqlite_session.commit()

    assert app1.human_id == f"APP-{year}-001"
    assert app2.human_id == f"APP-{year}-002"


def test_retries_on_collision_then_succeeds(sqlite_session, monkeypatch):
    """A concurrent create can hand back an id that already exists; the helper
    must roll back and recompute rather than crash."""
    year = datetime.now(timezone.utc).year
    job1 = _create_job(sqlite_session, job_id_canonical="SRC:c")
    insert_application_with_human_id(sqlite_session, _build_for(job1))
    sqlite_session.commit()

    job2 = _create_job(sqlite_session, job_id_canonical="SRC:d")
    # First call returns the already-taken id (simulating a lost race), then the
    # real next id on retry.
    ids = iter([f"APP-{year}-001", f"APP-{year}-002"])
    monkeypatch.setattr(human_id_mod, "next_human_id", lambda _session: next(ids))

    app2 = insert_application_with_human_id(sqlite_session, _build_for(job2))
    sqlite_session.commit()

    assert app2.human_id == f"APP-{year}-002"


def test_raises_when_all_attempts_collide(sqlite_session, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    year = datetime.now(timezone.utc).year
    job1 = _create_job(sqlite_session, job_id_canonical="SRC:e")
    insert_application_with_human_id(sqlite_session, _build_for(job1))
    sqlite_session.commit()

    job2 = _create_job(sqlite_session, job_id_canonical="SRC:f")
    monkeypatch.setattr(
        human_id_mod, "next_human_id", lambda _session: f"APP-{year}-001"
    )

    with pytest.raises(IntegrityError):
        insert_application_with_human_id(sqlite_session, _build_for(job2), max_attempts=3)
