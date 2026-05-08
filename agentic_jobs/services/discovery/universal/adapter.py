from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, Sequence

import httpx

from agentic_jobs.config import Settings
from agentic_jobs.core.enums import JobSourceType, SubmissionMode
from agentic_jobs.services.discovery.base import JobDetail, JobRef, SourceAdapter
from agentic_jobs.services.discovery.rate_limiter import AsyncRateLimiter
from agentic_jobs.services.discovery.universal.detector import ParserDetectionError, ParserDetector
from agentic_jobs.services.discovery.universal.parsers import BaseUniversalParser, build_parser
from agentic_jobs.services.discovery.universal.sites_config import (
    UniversalFeedConfig,
    UniversalSitesConfig,
    load_universal_sites_config,
)


class UniversalAdapter(SourceAdapter):
    source_name = "universal"
    source_display_name = "Universal"
    job_source_type = JobSourceType.COMPANY
    submission_mode = SubmissionMode.DEEPLINK
    uses_frontier = True

    def __init__(
        self,
        settings: Settings,
        *,
        sites_config: UniversalSitesConfig | None = None,
        client: httpx.AsyncClient | None = None,
        rate_limiter: AsyncRateLimiter | None = None,
    ) -> None:
        self.settings = settings
        timeout = httpx.Timeout(settings.request_timeout_seconds)
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        limiter_budget = max(settings.requests_per_minute // 2, 10)
        self._limiter = rate_limiter or AsyncRateLimiter(limiter_budget, 60.0)
        self._sites_config = sites_config or load_universal_sites_config(settings.universal_sites_config_path)
        self._feeds: Dict[str, UniversalFeedConfig] = {feed.slug: feed for feed in self._sites_config.feeds}
        self._parsers: Dict[str, BaseUniversalParser] = {}
        self._detector = ParserDetector(self._client)

    async def __aenter__(self) -> "UniversalAdapter":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def discover(self) -> Sequence[str]:
        return sorted(self._feeds.keys())

    async def list_jobs(self, org_slug: str) -> Sequence[JobRef]:
        feed = self._feeds.get(org_slug)
        if not feed:
            return []
        try:
            feed = await self._ensure_feed_ready(feed)
        except ParserDetectionError as exc:
            LOGGER.warning("Skipping feed %s due to detection error: %s", org_slug, exc)
            return []
        parser = self._get_parser(feed)
        parsed_jobs = await parser.list_jobs()
        filtered_jobs = self._filter_by_recency(parsed_jobs)
        job_refs: list[JobRef] = []
        for parsed in filtered_jobs:
            metadata = dict(parsed.metadata or {})
            metadata.update(
                {
                    "feed_slug": feed.feed_slug,
                    "site_slug": feed.site_slug,
                    "parser": feed.parser,
                    "source_label": feed.source_label,
                    "posted_at": parsed.posted_at.isoformat() if parsed.posted_at else None,
                    "company_override": parsed.company_name or feed.display_name,
                }
            )
            job_refs.append(
                JobRef(
                    source=self.source_name,
                    org_slug=feed.slug,
                    job_id=parsed.job_id,
                    title=parsed.title,
                    location=parsed.location or "Unknown",
                    detail_url=parsed.detail_url,
                    metadata=metadata,
                )
            )
        return job_refs

    async def fetch_job_detail(self, job_ref: JobRef) -> JobDetail:
        feed = self._feeds.get(job_ref.org_slug)
        if not feed:
            raise ValueError(f"No feed configuration found for {job_ref.org_slug}")
        try:
            resolved_feed = await self._ensure_feed_ready(feed)
        except ParserDetectionError as exc:
            raise ValueError(f"Feed {job_ref.org_slug} cannot resolve parser: {exc}") from exc
        parser = self._get_parser(resolved_feed) if resolved_feed else None
        if parser is None:
            raise ValueError(f"No feed configuration found for {job_ref.org_slug}")
        detail = await parser.fetch_job_detail(job_ref)
        if not detail.company_name:
            detail.company_name = job_ref.metadata.get("company_override") or (resolved_feed.display_name if resolved_feed else None)
        return detail

    def canonical_id(self, job_ref: JobRef) -> str:
        return f"{self.source_name.upper()}:{job_ref.org_slug}:{job_ref.job_id}"

    def get_crawl_interval_minutes(self, org_slug: str) -> int | None:
        feed = self._feeds.get(org_slug)
        if not feed:
            return None
        return feed.crawl_interval_minutes

    async def _ensure_feed_ready(self, feed: UniversalFeedConfig | None) -> UniversalFeedConfig:
        if feed is None:
            raise ParserDetectionError("Feed configuration missing.")
        if not feed.requires_detection:
            return feed
        if not feed.site_url:
            raise ParserDetectionError(f"Feed {feed.slug} is missing site_url for detection.")
        detection = await self._detector.detect(feed.site_url)
        feed.parser = detection.parser
        feed.options = detection.options
        return feed

    def _get_parser(self, feed: UniversalFeedConfig):
        if feed.slug not in self._parsers:
            self._parsers[feed.slug] = build_parser(feed.parser, feed, self._client, self._limiter)
        return self._parsers[feed.slug]

    def _filter_by_recency(self, jobs):
        max_age = getattr(self.settings, "universal_max_age_delta", None)
        if not max_age:
            return jobs
        cutoff = datetime.now(tz=timezone.utc) - max_age
        filtered: list = []
        for job in jobs:
            posted_at = getattr(job, "posted_at", None)
            if posted_at is None:
                filtered.append(job)
                continue
            if posted_at >= cutoff:
                filtered.append(job)
        return filtered
LOGGER = logging.getLogger(__name__)
