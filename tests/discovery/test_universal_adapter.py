import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from agentic_jobs.services.discovery.base import JobDetail, JobRef
from agentic_jobs.services.discovery.universal import adapter as adapter_module
from agentic_jobs.services.discovery.universal.adapter import UniversalAdapter
from agentic_jobs.services.discovery.universal.detector import DetectionResult
from agentic_jobs.services.discovery.universal.parsers import ParsedJob
from agentic_jobs.services.discovery.universal.sites_config import (
    UniversalFeedConfig,
    UniversalSitesConfig,
)


class DummyParser:
    def __init__(self, feed_config, *_args, **_kwargs) -> None:
        self.feed_config = feed_config

    async def list_jobs(self):
        metadata = {"dummy": True}
        now = datetime.now(tz=timezone.utc)
        return [
            ParsedJob(
                job_id="dummy-1",
                title="Systems Engineer",
                detail_url="https://example.com/job",
                location="Remote",
                company_name=self.feed_config.display_name,
                posted_at=now,
                metadata=metadata,
            ),
            ParsedJob(
                job_id="old-1",
                title="Old Role",
                detail_url="https://example.com/old",
                location="Remote",
                company_name=self.feed_config.display_name,
                posted_at=now - timedelta(days=30),
                metadata=metadata,
            )
        ]

    async def fetch_job_detail(self, job_ref: JobRef) -> JobDetail:
        return JobDetail(job_ref=job_ref, html="<p>Details</p>", company_name=self.feed_config.display_name)

    def canonical_id(self, job_ref: JobRef) -> str:
        return f"DUMMY:{job_ref.job_id}"


def test_universal_adapter_emits_job_refs(monkeypatch, test_settings):
    def _fake_builder(_parser_name, feed_config, *_args, **_kwargs):
        return DummyParser(feed_config)

    monkeypatch.setattr(adapter_module, "build_parser", _fake_builder)
    class DummyDetector:
        async def detect(self, site_url: str) -> DetectionResult:
            return DetectionResult(
                parser="workday",
                options={"host": "jobs.apple.com", "tenant": "apple", "site": "en-us"},
            )

    monkeypatch.setattr(adapter_module, "ParserDetector", lambda client: DummyDetector())
    feed = UniversalFeedConfig(
        site_slug="apple",
        display_name="Apple Careers",
        feed_slug="default",
        parser=None,
        crawl_interval_minutes=120,
        options={},
        site_url="https://jobs.apple.com/en-us/search",
    )
    sites_config = UniversalSitesConfig(feeds=[feed])
    async def _run() -> None:
        async with httpx.AsyncClient() as client:
            universal = UniversalAdapter(test_settings, sites_config=sites_config, client=client)
            jobs = await universal.list_jobs(feed.slug)
            assert len(jobs) == 1
            job_ref = jobs[0]
            assert job_ref.metadata["source_label"] == "Apple Careers"
            detail = await universal.fetch_job_detail(job_ref)
            assert detail.company_name == "Apple Careers"
            assert universal.get_crawl_interval_minutes(feed.slug) == 120
            await universal.aclose()

    asyncio.run(_run())
