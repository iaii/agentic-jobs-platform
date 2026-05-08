from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

import httpx

from agentic_jobs.services.discovery.base import DiscoveryError, JobDetail, JobRef
from agentic_jobs.services.discovery.rate_limiter import AsyncRateLimiter
from agentic_jobs.services.discovery.universal.sites_config import UniversalFeedConfig


@dataclass(slots=True)
class ParsedJob:
    job_id: str
    title: str
    detail_url: str
    location: str
    company_name: str | None
    posted_at: datetime | None
    metadata: dict[str, Any]


class BaseUniversalParser:
    """Base helper that concrete ATS parsers extend."""

    parser_name = "base"

    def __init__(
        self,
        feed_config: UniversalFeedConfig,
        client: httpx.AsyncClient,
        rate_limiter: AsyncRateLimiter,
    ) -> None:
        self.feed_config = feed_config
        self._client = client
        self._limiter = rate_limiter

    async def list_jobs(self) -> Sequence[ParsedJob]:
        raise NotImplementedError

    async def fetch_job_detail(self, job_ref: JobRef) -> JobDetail:
        raise NotImplementedError

    def canonical_id(self, job_ref: JobRef) -> str:
        return job_ref.job_id

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            timestamp = float(value)
            if timestamp > 1e12:
                timestamp = timestamp / 1000
            try:
                return datetime.fromtimestamp(timestamp, tz=timezone.utc)
            except (ValueError, OSError, OverflowError):
                return None
        if isinstance(value, str):
            candidates = [value, value.replace("Z", "+00:00")]
            for candidate in candidates:
                try:
                    parsed = datetime.fromisoformat(candidate)
                except ValueError:
                    parsed = None
                if parsed:
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    return parsed
            formats = ("%Y-%m-%d", "%m/%d/%Y")
            for fmt in formats:
                try:
                    parsed = datetime.strptime(value, fmt)
                except ValueError:
                    continue
                else:
                    return parsed.replace(tzinfo=timezone.utc)
        return None


class LeverParser(BaseUniversalParser):
    parser_name = "lever"

    def _build_api_url(self) -> str:
        options = self.feed_config.options
        custom_url = options.get("api_url")
        if isinstance(custom_url, str) and custom_url.strip():
            return custom_url.strip()
        company = options.get("company") or self.feed_config.site_slug
        company_slug = str(company).strip()
        if not company_slug:
            raise DiscoveryError("Lever parser requires a company slug or api_url")
        return f"https://api.lever.co/v0/postings/{company_slug}?mode=json"

    async def list_jobs(self) -> Sequence[ParsedJob]:
        url = self._build_api_url()
        try:
            async with self._limiter:
                response = await self._client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise DiscoveryError(f"Lever request failed for {self.feed_config.slug}") from exc
        payload = response.json()
        if not isinstance(payload, list):
            raise DiscoveryError("Unexpected Lever payload shape")

        jobs: list[ParsedJob] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            job_id = str(item.get("id") or item.get("lever_id") or "")
            if not job_id:
                continue
            title = str(item.get("text") or item.get("title") or "Untitled Role")
            detail_url = (
                item.get("hostedUrl")
                or item.get("applyUrl")
                or item.get("urls", {}).get("show")
            )
            if not detail_url:
                continue
            categories = item.get("categories") or {}
            location = categories.get("location") or "Unknown"
            posted_at = self._parse_timestamp(item.get("createdAt"))
            metadata = {"lever_item": item}
            jobs.append(
                ParsedJob(
                    job_id=job_id,
                    title=title,
                    detail_url=detail_url,
                    location=location,
                    company_name=item.get("company"),
                    posted_at=posted_at,
                    metadata=metadata,
                )
            )
        return jobs

    async def fetch_job_detail(self, job_ref: JobRef) -> JobDetail:
        item = job_ref.metadata.get("lever_item") or {}
        description = item.get("description") or ""
        additional = item.get("additional") or ""
        lists = item.get("lists") or []

        sections: list[str] = [
            f"<h1>{job_ref.title}</h1>",
            f"<p><strong>Company:</strong> {job_ref.metadata.get('company_override') or self.feed_config.display_name}</p>",
            f"<p><strong>Location:</strong> {job_ref.location}</p>",
            f"<p><strong>Source URL:</strong> <a href='{job_ref.detail_url}'>{job_ref.detail_url}</a></p>",
        ]
        if description:
            sections.append(f"<h2>Description</h2><p>{description}</p>")
        if additional:
            sections.append(f"<h2>Additional Information</h2><p>{additional}</p>")

        def _render_lists(entries: Iterable[dict[str, Any]]) -> None:
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                text = entry.get("text")
                content = entry.get("content")
                if not content:
                    continue
                sections.append(f"<h2>{text or 'Details'}</h2>")
                bullets = "".join(f"<li>{row}</li>" for row in content if row)
                if bullets:
                    sections.append(f"<ul>{bullets}</ul>")

        _render_lists(lists)
        html = "\n".join(sections)
        return JobDetail(job_ref=job_ref, html=html, company_name=item.get("company"), metadata={"lever_item": item})


class WorkdayParser(BaseUniversalParser):
    parser_name = "workday"

    def __init__(
        self,
        feed_config: UniversalFeedConfig,
        client: httpx.AsyncClient,
        rate_limiter: AsyncRateLimiter,
    ) -> None:
        super().__init__(feed_config, client, rate_limiter)
        options = feed_config.options
        host = options.get("host")
        tenant = options.get("tenant")
        site = options.get("site")
        if not all(isinstance(val, str) and val.strip() for val in (host, tenant, site)):
            raise DiscoveryError("Workday parser requires host, tenant, and site options")
        base = options.get("base_path") or f"https://{host}/wday/cxs/{tenant}/{site}"
        self._base_url = base.rstrip("/")
        self._jobs_url = f"{self._base_url}/jobs"
        self._job_detail_url = f"{self._base_url}/job"
        self._page_size = max(1, int(options.get("page_size") or 50))
        self._max_pages = max(1, int(options.get("max_pages") or 5))
        self._search_text = str(options.get("search_text") or "")
        facets = options.get("facets")
        self._facets = facets if isinstance(facets, dict) else {}

    async def list_jobs(self) -> Sequence[ParsedJob]:
        jobs: list[ParsedJob] = []
        offset = 0

        for _ in range(self._max_pages):
            payload = {
                "appliedFacets": self._facets,
                "limit": self._page_size,
                "offset": offset,
                "searchText": self._search_text,
            }
            try:
                async with self._limiter:
                    response = await self._client.post(self._jobs_url, json=payload)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise DiscoveryError(f"Workday request failed for {self.feed_config.slug}") from exc
            data = response.json()
            postings = data.get("jobPostings") or []
            if not postings:
                break

            for posting in postings:
                if not isinstance(posting, dict):
                    continue
                bullet_fields_raw = posting.get("bulletFields")
                bullet_fields = bullet_fields_raw if isinstance(bullet_fields_raw, list) else []
                first_bullet = bullet_fields[0] if bullet_fields else ""
                job_id = str(posting.get("jobPostingId") or posting.get("externalPath") or first_bullet or "")
                if not job_id:
                    continue
                title = posting.get("title") or first_bullet or "Untitled Role"
                external_url = posting.get("externalUrl")
                external_path = posting.get("externalPath")
                detail_url = external_url or (f"{self._base_url}{external_path}" if external_path else "")
                if not detail_url:
                    continue
                location = posting.get("locationsText") or posting.get("secondaryLocation") or "Unknown"
                posted_at = self._parse_timestamp(posting.get("postedOn") or posting.get("startDate"))
                metadata = {"workday_posting": posting}
                jobs.append(
                    ParsedJob(
                        job_id=job_id,
                        title=title,
                        detail_url=detail_url,
                        location=location,
                        company_name=self.feed_config.display_name,
                        posted_at=posted_at,
                        metadata=metadata,
                    )
                )
            offset += self._page_size
        return jobs

    async def fetch_job_detail(self, job_ref: JobRef) -> JobDetail:
        posting = job_ref.metadata.get("workday_posting") or {}
        posting_id = posting.get("jobPostingId")
        detail_data: dict[str, Any] = {}
        if posting_id:
            detail_url = f"{self._job_detail_url}/{posting_id}"
            try:
                async with self._limiter:
                    response = await self._client.get(detail_url)
                if response.status_code == 200:
                    detail_data = response.json()
            except httpx.HTTPError:
                detail_data = {}
        job_info = detail_data.get("jobPostingInfo") if isinstance(detail_data, dict) else {}
        description = job_info.get("jobDescription") or job_info.get("jobPostingDescription", "")
        qualifications = job_info.get("qualificationsDescription")

        sections = [
            f"<h1>{job_ref.title}</h1>",
            f"<p><strong>Company:</strong> {self.feed_config.display_name}</p>",
            f"<p><strong>Location:</strong> {job_ref.location}</p>",
            f"<p><strong>Source URL:</strong> <a href='{job_ref.detail_url}'>{job_ref.detail_url}</a></p>",
        ]
        if description:
            sections.append(f"<h2>Description</h2><p>{description}</p>")
        responsibilities = job_info.get("responsibilitiesDescription")
        if responsibilities:
            sections.append(f"<h2>Responsibilities</h2><p>{responsibilities}</p>")
        if qualifications:
            sections.append(f"<h2>Qualifications</h2><p>{qualifications}</p>")
        html = "\n".join(sections)
        metadata = {"workday_detail": detail_data, "workday_posting": posting}
        return JobDetail(job_ref=job_ref, html=html, company_name=self.feed_config.display_name, metadata=metadata)


PARSER_REGISTRY = {
    LeverParser.parser_name: LeverParser,
    WorkdayParser.parser_name: WorkdayParser,
}


def build_parser(
    parser_name: str,
    feed_config: UniversalFeedConfig,
    client: httpx.AsyncClient,
    limiter: AsyncRateLimiter,
) -> BaseUniversalParser:
    normalized = parser_name.strip().lower()
    parser_cls = PARSER_REGISTRY.get(normalized)
    if not parser_cls:
        raise DiscoveryError(f"Unknown universal parser '{parser_name}'")
    return parser_cls(feed_config, client, limiter)
