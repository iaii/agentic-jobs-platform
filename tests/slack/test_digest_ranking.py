from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy.orm import Session

from agentic_jobs.db import models
from agentic_jobs.services.slack.workflows import collect_digest_rows


def _mk_job(title: str, jd: str, loc: str, scraped_at: datetime) -> models.Job:
    j = models.Job(
        title=title,
        company_name="Acme",
        location=loc,
        url="https://example/j",
        source_type=models.JobSourceType.GREENHOUSE,  # type: ignore[attr-defined]
        domain_root="boards.greenhouse.io",
        submission_mode=models.SubmissionMode.ATS,  # type: ignore[attr-defined]
        jd_text=jd,
        requirements=[],
        job_id_canonical=f"GH:{uuid.uuid4().hex[:6]}",
        scraped_at=scraped_at,
        hash=str(uuid.uuid4()),
    )
    return j


def test_digest_sorting_by_score_then_time(sqlite_session: Session) -> None:
    now = datetime.now(tz=timezone.utc)
    j1 = _mk_job("SWE Backend", "new grad Python", "NYC", now - timedelta(hours=1))
    j2 = _mk_job("SWE Backend", "new grad Python", "NYC", now)
    j3 = _mk_job("Engineer", "", "Somewhere", now)

    sqlite_session.add_all([j1, j2, j3])
    sqlite_session.commit()

    rows = collect_digest_rows(
        sqlite_session,
        since=now - timedelta(days=1),
        digest_day=now.date(),
        limit=10,
    )

    # j1 and j2 should have same score; j2 newer should come first
    ids = [r.job_id for r in rows]
    assert ids.index(j2.id) < ids.index(j1.id)


def test_rationale_included_and_truncated_in_digest_blocks() -> None:
    # Rendering/truncation is covered in digest test for blocks; duplicated here for ordering test completeness
    from agentic_jobs.services.slack.digest import build_digest_blocks, DigestRow

    long_rationale = " + ".join(["tag"] * 100)
    row = DigestRow(
        job_id=uuid.uuid4(),
        title="Engineer",
        company="Acme",
        location="NYC",
        url="https://example/j",
        score=0.99,
        rationale=long_rationale,
    )
    blocks = build_digest_blocks([row])
    assert "..." in blocks[0]["text"]["text"]


