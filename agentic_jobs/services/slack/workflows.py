from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_jobs.core.enums import DomainReviewStatus
from agentic_jobs.db import models
from agentic_jobs.services.ranking import score_job
from agentic_jobs.services.slack.digest import DigestRow, NeedsReviewCard


def collect_digest_rows(
    session: Session,
    *,
    since: datetime,
    digest_day: date,
    limit: int,
) -> list[DigestRow]:
    posted_job_ids = {
        row
        for row in session.execute(
            select(models.DigestLog.job_id).where(models.DigestLog.digest_date == digest_day)
        ).scalars()
    }

    jobs = list(
        session.execute(
            select(models.Job)
            .where(models.Job.scraped_at >= since)
            .order_by(models.Job.scraped_at.desc())
        ).scalars()
    )

    rows: list[DigestRow] = []
    for job in jobs:
        if job.id in posted_job_ids:
            continue
        score_result = score_job(job)
        rows.append(
            DigestRow(
                job_id=job.id,
                title=job.title,
                company=job.company_name,
                location=job.location,
                url=job.url,
                score=score_result.score,
                rationale=score_result.rationale,
            )
        )

    # Sort by score desc, tie-break by scraped_at desc
    # Attach scraped_at alongside for sorting; not part of DigestRow to keep blocks stable
    job_time_map = {j.id: j.scraped_at for j in jobs}
    rows_with_time = [(row, job_time_map.get(row.job_id)) for row in rows]
    rows_with_time.sort(key=lambda item: (item[0].score, item[1]), reverse=True)
    rows = [row for row, _t in rows_with_time]
    return rows[:limit]


def record_digest_post(
    session: Session,
    *,
    rows: Iterable[DigestRow],
    digest_day: date,
    channel_id: str,
    message_ts: str,
) -> None:
    entries = [
        models.DigestLog(
            job_id=row.job_id,
            digest_date=digest_day,
            slack_channel_id=channel_id,
            slack_message_ts=message_ts,
        )
        for row in rows
    ]
    session.add_all(entries)
    session.commit()


@dataclass(slots=True)
class NeedsReviewCandidate:
    record: models.DomainReview
    card: NeedsReviewCard


def collect_needs_review_candidates(
    session: Session,
    *,
    since: datetime,
) -> list[NeedsReviewCandidate]:
    now_utc = datetime.now(tz=timezone.utc)
    jobs = list(
        session.execute(
            select(models.Job)
            .where(models.Job.scraped_at >= since)
            .order_by(models.Job.scraped_at.desc())
        ).scalars()
    )

    candidates: list[NeedsReviewCandidate] = []
    seen_domains: set[str] = set()

    for job in jobs:
        if job.domain_root in seen_domains:
            continue

        whitelist_entry = session.get(models.Whitelist, job.domain_root)
        if whitelist_entry:
            continue

        domain_review = session.execute(
            select(models.DomainReview)
            .where(models.DomainReview.domain_root == job.domain_root)
            .order_by(models.DomainReview.created_at.desc())
            .limit(1)
        ).scalars().first()

        if domain_review:
            if domain_review.status is DomainReviewStatus.APPROVED:
                continue
            if (
                domain_review.status is DomainReviewStatus.MUTED
                and domain_review.muted_until
                and domain_review.muted_until > now_utc
            ):
                continue
            if domain_review.status is DomainReviewStatus.PENDING:
                continue
            domain_review.status = DomainReviewStatus.PENDING
            domain_review.muted_until = None
        else:
            domain_review = models.DomainReview(
                domain_root=job.domain_root,
                status=DomainReviewStatus.PENDING,
                company_name=job.company_name,
                ats_type=job.source_type.value,
            )
            session.add(domain_review)
            session.flush()
        domain_review.company_name = job.company_name or domain_review.company_name
        domain_review.ats_type = job.source_type.value

        trust_event = session.execute(
            select(models.TrustEvent)
            .where(models.TrustEvent.domain_root == job.domain_root)
            .order_by(models.TrustEvent.created_at.desc())
            .limit(1)
        ).scalars().first()

        if trust_event and trust_event.verdict.value == "auto-safe":
            continue

        score = trust_event.score if trust_event else 0
        verdict = trust_event.verdict.value if trust_event else "needs-review"

        card = NeedsReviewCard(
            domain_root=job.domain_root,
            sample_url=job.url,
            company_name=job.company_name,
            score=score,
            verdict=verdict,
        )
        candidates.append(NeedsReviewCandidate(record=domain_review, card=card))
        seen_domains.add(job.domain_root)

    session.commit()
    return candidates
