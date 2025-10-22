from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from agentic_jobs.core.enums import JobSourceType, SubmissionMode, TrustVerdict
from agentic_jobs.db import models
from agentic_jobs.services.discovery.orchestrator import run_discovery


def test_discover_from_sitemap_returns_slugs(greenhouse_adapter) -> None:
    slugs = asyncio.run(greenhouse_adapter.discover_from_sitemap())
    assert slugs == ["example-startup", "testorg"]


def test_list_jobs_from_json_feed(greenhouse_adapter) -> None:
    jobs = asyncio.run(greenhouse_adapter.list_jobs("testorg"))
    assert len(jobs) == 1
    job = jobs[0]
    assert job.job_id == "12345"
    assert job.title == "Software Engineer"
    assert job.location == "Remote - US"


def test_run_discovery_inserts_jobs(
    sqlite_session,
    greenhouse_adapter,
    test_settings,
) -> None:
    summary = asyncio.run(run_discovery(sqlite_session, [greenhouse_adapter], test_settings))

    assert summary.orgs_crawled == 2
    assert summary.jobs_seen == 2
    assert summary.jobs_inserted == 2
    assert summary.domains_scored == 1

    jobs = list(sqlite_session.execute(select(models.Job)).scalars())
    assert len(jobs) == summary.jobs_inserted
    for job in jobs:
        assert job.job_id_canonical.startswith("GH:")
        assert job.source_type is JobSourceType.GREENHOUSE
        assert job.submission_mode is SubmissionMode.ATS
        assert job.domain_root == "boards.greenhouse.io"
        assert job.jd_text
        assert job.requirements

    job_sources = list(sqlite_session.execute(select(models.JobSource)).scalars())
    assert len(job_sources) == len(jobs)
    for job_source in job_sources:
        assert job_source.source_type is JobSourceType.GREENHOUSE

    trust_events = list(sqlite_session.execute(select(models.TrustEvent)).scalars())
    assert len(trust_events) == len(jobs)
    for trust_event in trust_events:
        assert trust_event.domain_root == "boards.greenhouse.io"
        assert trust_event.verdict is TrustVerdict.AUTO_SAFE

    # Second run should deduplicate and insert nothing.
    summary_repeat = asyncio.run(run_discovery(sqlite_session, [greenhouse_adapter], test_settings))
    assert summary_repeat.jobs_inserted == 0


def test_run_discovery_with_github_adapters(
    sqlite_session,
    greenhouse_adapter,
    github_adapters,
    test_settings,
) -> None:
    adapters = [greenhouse_adapter] + github_adapters
    summary = asyncio.run(run_discovery(sqlite_session, adapters, test_settings))

    assert summary.orgs_crawled == 4
    assert summary.jobs_seen == 4
    assert summary.jobs_inserted == 4
    assert summary.domains_scored >= 2
