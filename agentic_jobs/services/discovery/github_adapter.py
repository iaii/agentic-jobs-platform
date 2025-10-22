from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Sequence

import httpx

from agentic_jobs.config import Settings
from agentic_jobs.core.enums import JobSourceType, SubmissionMode
from agentic_jobs.services.discovery.base import DiscoveryError, JobDetail, JobRef, SourceAdapter
from agentic_jobs.services.discovery.rate_limiter import AsyncRateLimiter


def _first_non_empty(*values: Any, default: str | None = None) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    return item.strip()
    return default


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item)
    if isinstance(value, dict):
        return json.dumps(value)
    return str(value)


class GithubPositionsAdapter(SourceAdapter):
    """Adapter that ingests open-source GitHub position JSON feeds."""

    job_source_type = JobSourceType.COMPANY
    submission_mode = SubmissionMode.DEEPLINK
    uses_frontier = False

    def __init__(
        self,
        settings: Settings,
        *,
        source_name: str,
        slug: str,
        data_urls: Sequence[str],
        client: httpx.AsyncClient | None = None,
        rate_limiter: AsyncRateLimiter | None = None,
    ) -> None:
        self.source_name = source_name
        self._slug = slug
        self._data_urls = [url for url in data_urls if url]
        self._timeout = httpx.Timeout(settings.request_timeout_seconds)
        self._client = client or httpx.AsyncClient(timeout=self._timeout)
        self._owns_client = client is None
        calls_per_minute = max(settings.requests_per_minute // 2, 10)
        self._limiter = rate_limiter or AsyncRateLimiter(calls_per_minute, 60.0)
        self._max_age_delta = settings.github_max_age_delta
        if not self._data_urls:
            raise ValueError("GithubPositionsAdapter requires at least one data URL")

    async def __aenter__(self) -> "GithubPositionsAdapter":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def discover(self) -> Sequence[str]:
        return [self._slug]

    async def list_jobs(self, org_slug: str) -> Sequence[JobRef]:
        data = await self._fetch_positions()
        items = list(self._flatten_positions(data))
        jobs: list[JobRef] = []
        now = datetime.utcnow().replace(tzinfo=timezone.utc)
        for item in items:
            posted_at = self._extract_posted_at(item)
            if posted_at is None:
                continue
            if self._max_age_delta and posted_at < now - self._max_age_delta:
                continue

            company = _first_non_empty(
                item.get("company"),
                item.get("org"),
                item.get("organization"),
                default="Unknown Company",
            )
            title = _first_non_empty(
                item.get("title"),
                item.get("position"),
                item.get("role"),
                default="Untitled Role",
            )
            url = _first_non_empty(
                item.get("url"),
                item.get("application_link"),
                item.get("link"),
            )
            if not url:
                continue

            job_id = self._derive_job_id(item, url)
            location = _first_non_empty(
                item.get("location"),
                item.get("locations"),
                item.get("city"),
                default="Unknown",
            ) or "Unknown"
            jobs.append(
                JobRef(
                    source=self.source_name,
                    org_slug=org_slug,
                    job_id=job_id,
                    title=title,
                    location=location,
                    detail_url=url,
                    metadata={"item": item, "company": company, "posted_at": posted_at.isoformat()},
                )
            )

        return jobs

    async def fetch_job_detail(self, job_ref: JobRef) -> JobDetail:
        item: dict[str, Any] = job_ref.metadata.get("item", {})
        company_name = job_ref.metadata.get("company") or _first_non_empty(
            item.get("company"),
            item.get("org"),
            item.get("organization"),
            default="Unknown Company",
        )

        html = self._build_detail_html(job_ref, item, company_name)
        return JobDetail(
            job_ref=job_ref,
            html=html,
            company_name=company_name,
            metadata={"source_item": item},
        )

    def canonical_id(self, job_ref: JobRef) -> str:
        return f"{self.source_name.upper()}:{job_ref.job_id}"

    async def _fetch_positions(self) -> Any:
        last_error: Exception | None = None
        for url in self._data_urls:
            try:
                async with self._limiter:
                    response = await self._client.get(url)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as exc:
                last_error = exc
                continue
        if last_error:
            raise DiscoveryError(f"Failed to fetch positions for {self.source_name}") from last_error
        raise DiscoveryError(f"No data URLs configured for {self.source_name}")

    def _flatten_positions(self, payload: Any) -> Iterable[dict[str, Any]]:
        if isinstance(payload, list):
            yield from (item for item in payload if isinstance(item, dict))
            return

        if isinstance(payload, dict):
            if "positions" in payload and isinstance(payload["positions"], list):
                yield from (
                    item for item in payload["positions"] if isinstance(item, dict)
                )
                return

            if "listings" in payload and isinstance(payload["listings"], list):
                yield from (
                    item for item in payload["listings"] if isinstance(item, dict)
                )
                return

            if "companies" in payload and isinstance(payload["companies"], list):
                for company_entry in payload["companies"]:
                    if not isinstance(company_entry, dict):
                        continue
                    company_name = company_entry.get("company") or company_entry.get("name")
                    roles = company_entry.get("roles") or company_entry.get("positions")
                    if isinstance(roles, list):
                        for role in roles:
                            if isinstance(role, dict):
                                merged = {"company": company_name, **role}
                                yield merged
                return

            for value in payload.values():
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            yield item
                elif isinstance(value, dict):
                    yield value

    def _derive_job_id(self, item: dict[str, Any], url: str) -> str:
        if "id" in item and isinstance(item["id"], (str, int)):
            return str(item["id"])
        if "slug" in item and isinstance(item["slug"], str):
            return item["slug"]
        hash_input = url.lower().strip().encode("utf-8")
        return hashlib.sha1(hash_input).hexdigest()

    def _build_detail_html(self, job_ref: JobRef, item: dict[str, Any], company: str) -> str:
        sections: List[str] = [
            f"<h1>{job_ref.title}</h1>",
            f"<p><strong>Company:</strong> {company}</p>",
            f"<p><strong>Location:</strong> {_stringify(item.get('location') or item.get('locations') or job_ref.location)}</p>",
            f"<p><strong>Source URL:</strong> {job_ref.detail_url}</p>",
        ]

        description = _first_non_empty(
            item.get("description"),
            item.get("notes"),
            item.get("about"),
        )
        if description:
            sections.append(f"<div><p>{description}</p></div>")

        qualifications = item.get("qualifications") or item.get("requirements")
        if isinstance(qualifications, list):
            bullets = "".join(f"<li>{_stringify(entry)}</li>" for entry in qualifications if entry)
            if bullets:
                sections.append(f"<h2>Requirements</h2><ul>{bullets}</ul>")
        elif isinstance(qualifications, str):
            sections.append(f"<h2>Requirements</h2><p>{qualifications}</p>")

        return "\n".join(sections)

    def _extract_posted_at(self, item: dict[str, Any]) -> datetime | None:
        date_fields = [
            "date_posted",
            "posted",
            "date",
            "listed_at",
            "created_at",
            "updated_at",
            "timestamp",
            "added_at",
        ]

        for key in date_fields:
            if key not in item:
                continue
            value = item[key]
            parsed = self._parse_date(value)
            if parsed is not None:
                return parsed
        return None

    def _parse_date(self, value: Any) -> datetime | None:
        if isinstance(value, (int, float)):
            # assume seconds; if large assume ms
            timestamp = float(value)
            if timestamp > 1e12:
                timestamp /= 1000
            try:
                return datetime.fromtimestamp(timestamp, tz=timezone.utc)
            except (OSError, OverflowError):
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

            strptime_patterns = [
                "%Y-%m-%d",
                "%m/%d/%Y",
                "%Y/%m/%d",
            ]
            for pattern in strptime_patterns:
                try:
                    parsed = datetime.strptime(value, pattern).replace(tzinfo=timezone.utc)
                    return parsed
                except ValueError:
                    continue
        return None
