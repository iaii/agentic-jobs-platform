from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, Sequence
from urllib.parse import urlparse

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from agentic_jobs.config import Settings
from agentic_jobs.core.enums import JobSourceType, SubmissionMode
from agentic_jobs.db import models
from agentic_jobs.services.discovery.base import DiscoveryError, DiscoverySummary, JobRef, SourceAdapter
from agentic_jobs.services.sources.normalize import compute_hash, extract_requirements, html_to_text
from agentic_jobs.services.trust.evaluator import TrustResult, evaluate


def _slug_to_company(slug: str) -> str:
    parts = slug.replace("_", "-").split("-")
    return " ".join(part.capitalize() for part in parts if part)

LOGGER = logging.getLogger(__name__)


async def run_discovery(
    session: Session,
    adapters: Sequence[SourceAdapter],
    settings: Settings,
) -> DiscoverySummary:
    summary = DiscoverySummary()
    domain_roots: set[str] = set()

    for adapter in adapters:
        try:
            adapter_summary, adapter_domains = await _run_for_adapter(session, adapter, settings)
        except DiscoveryError as exc:
            LOGGER.warning("Skipping adapter %s due to error: %s", adapter.source_name, exc)
            continue

        summary.orgs_crawled += adapter_summary.orgs_crawled
        summary.jobs_seen += adapter_summary.jobs_seen
        summary.jobs_inserted += adapter_summary.jobs_inserted
        domain_roots.update(adapter_domains)

    summary.domains_scored = len(domain_roots)
    return summary


async def _run_for_adapter(
    session: Session,
    adapter: SourceAdapter,
    settings: Settings,
) -> tuple[DiscoverySummary, set[str]]:
    summary = DiscoverySummary()
    domain_cache: Dict[str, TrustResult] = {}
    cutoff = datetime.utcnow() - timedelta(days=30)

    if getattr(adapter, "uses_frontier", True):
        await _seed_frontier(session, adapter)

        frontier_records = _select_frontier(session, adapter, settings.max_orgs_per_run)
        if not frontier_records:
            return summary, set()

        for frontier in frontier_records:
            job_refs = await adapter.list_jobs(frontier.org_slug)
            summary.orgs_crawled += 1
            summary.jobs_seen += len(job_refs)

            for job_ref in job_refs:
                inserted = await _ingest_job(session, adapter, job_ref, cutoff, domain_cache)
                if inserted:
                    summary.jobs_inserted += 1

            frontier.last_crawled_at = datetime.utcnow()
    else:
        slugs = await adapter.discover()
        if not slugs:
            return summary, set()
        for slug in slugs:
            job_refs = await adapter.list_jobs(slug)
            summary.orgs_crawled += 1
            summary.jobs_seen += len(job_refs)
            for job_ref in job_refs:
                inserted = await _ingest_job(session, adapter, job_ref, cutoff, domain_cache)
                if inserted:
                    summary.jobs_inserted += 1

    session.commit()
    return summary, set(domain_cache.keys())


async def _seed_frontier(session: Session, adapter: SourceAdapter) -> None:
    slugs = await adapter.discover()
    if not slugs:
        return

    existing_slugs = {
        row
        for row in session.execute(
            select(models.FrontierOrg.org_slug).where(models.FrontierOrg.source == adapter.source_name)
        ).scalars()
    }

    new_slugs = [slug for slug in slugs if slug not in existing_slugs]
    if not new_slugs:
        return

    now = datetime.utcnow()
    session.add_all(
        [
            models.FrontierOrg(
                source=adapter.source_name,
                org_slug=slug,
                priority=100,
                discovered_at=now,
            )
            for slug in new_slugs
        ]
    )
    session.commit()


def _select_frontier(session: Session, adapter: SourceAdapter, limit: int) -> list[models.FrontierOrg]:
    now = datetime.utcnow()
    stmt = (
        select(models.FrontierOrg)
        .where(models.FrontierOrg.source == adapter.source_name)
        .where(or_(models.FrontierOrg.muted_until.is_(None), models.FrontierOrg.muted_until <= now))
        .order_by(models.FrontierOrg.priority.asc(), models.FrontierOrg.last_crawled_at.asc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars())


async def _ingest_job(
    session: Session,
    adapter: SourceAdapter,
    job_ref: JobRef,
    cutoff: datetime,
    domain_cache: Dict[str, TrustResult],
) -> bool:
    canonical_id = adapter.canonical_id(job_ref)
    if _job_seen_recently(session, canonical_id, cutoff):
        return False

    job_detail = await adapter.fetch_job_detail(job_ref)
    company_name = job_detail.company_name or _slug_to_company(job_ref.org_slug)
    jd_text = html_to_text(job_detail.html)
    requirements = extract_requirements(job_detail.html)
    job_hash = compute_hash(job_ref.title, company_name, jd_text)

    if _hash_seen_recently(session, job_hash, cutoff):
        return False

    domain_root = urlparse(job_ref.detail_url).netloc.lower()
    trust_result = await _evaluate_domain(domain_root, job_ref.detail_url, domain_cache)

    raw_payload = {
        "job_ref": {
            "job_id": job_ref.job_id,
            "title": job_ref.title,
            "location": job_ref.location,
            "detail_url": job_ref.detail_url,
            "metadata": job_ref.metadata,
        },
        "detail": job_detail.metadata,
    }

    source_type = getattr(adapter, "job_source_type", JobSourceType.COMPANY)
    submission_mode = getattr(adapter, "submission_mode", SubmissionMode.DEEPLINK)

    job_source = models.JobSource(
        source_type=source_type,
        source_url=job_ref.detail_url,
        company_name=company_name,
        domain_root=domain_root,
        raw_payload=raw_payload,
        hash=job_hash,
    )

    job = models.Job(
        title=job_ref.title,
        company_name=company_name,
        location=job_ref.location,
        url=job_ref.detail_url,
        source_type=source_type,
        domain_root=domain_root,
        submission_mode=submission_mode,
        jd_text=jd_text,
        requirements=requirements,
        job_id_canonical=canonical_id,
        scraped_at=datetime.utcnow(),
        hash=job_hash,
    )

    trust_event = models.TrustEvent(
        domain_root=domain_root,
        url=job_ref.detail_url,
        score=trust_result.score,
        signals=trust_result.signals,
        verdict=trust_result.verdict,
    )

    session.add(job_source)
    session.add(job)
    session.add(trust_event)
    session.flush()
    return True


def _job_seen_recently(session: Session, canonical_id: str, cutoff: datetime) -> bool:
    stmt = (
        select(models.Job.id)
        .where(models.Job.job_id_canonical == canonical_id)
    )
    return session.execute(stmt).scalar() is not None


def _hash_seen_recently(session: Session, job_hash: str, cutoff: datetime) -> bool:
    stmt = select(models.Job.id).where(models.Job.hash == job_hash)
    return session.execute(stmt).scalar() is not None


async def _evaluate_domain(
    domain_root: str,
    url: str,
    cache: Dict[str, TrustResult],
) -> TrustResult:
    if domain_root not in cache:
        cache[domain_root] = await evaluate(url, domain_root)
    return cache[domain_root]
