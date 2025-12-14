from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Sequence
from urllib.parse import urlparse

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
                if isinstance(item, dict):
                    candidate = _first_non_empty(item, default=None)
                    if candidate:
                        return candidate
        if isinstance(value, dict):
            for key in ("company", "name", "title", "organization"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
                if isinstance(candidate, list) or isinstance(candidate, dict):
                    nested = _first_non_empty(candidate, default=None)
                    if nested:
                        return nested
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


def _slug_to_company_name(slug: str | None) -> str | None:
    if not slug:
        return None
    cleaned = slug.replace("_", "-")
    parts = [part for part in cleaned.split("-") if part]
    if not parts:
        return None
    return " ".join(part.capitalize() for part in parts)


def _infer_company_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path_parts = [part for part in parsed.path.split("/") if part]

    if host.endswith("lever.co") and path_parts:
        return _slug_to_company_name(path_parts[0])
    if host.endswith("greenhouse.io") and path_parts:
        return _slug_to_company_name(path_parts[0])
    if host.endswith("myworkdayjobs.com") and path_parts:
        return _slug_to_company_name(path_parts[0])
    if host.endswith("icims.com"):
        return _slug_to_company_name(host.split(".")[0])
    if host.startswith("jobs.") and len(host.split(".")) > 2:
        return _slug_to_company_name(host.split(".")[1])
    return _slug_to_company_name(host.split(".")[0])


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
            company = _first_non_empty(
                item.get("company"),
                item.get("company_name"),
                item.get("org"),
                item.get("organization"),
            )
            if not company:
                company = (
                    _infer_company_from_url(item.get("company_url"))
                    or _infer_company_from_url(url)
                    or "Unknown Company"
                )

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
            item.get("company_name"),
            item.get("org"),
            item.get("organization"),
        ) or _infer_company_from_url(item.get("company_url")) or _infer_company_from_url(job_ref.detail_url) or "Unknown Company"

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
            f"<p><strong>Source URL:</strong> <a href='{job_ref.detail_url}'>{job_ref.detail_url}</a></p>",
        ]

        overview = _first_non_empty(
            item.get("description"),
            item.get("notes"),
            item.get("about"),
            item.get("summary"),
        )
        qualifications = item.get("qualifications") or item.get("requirements")
        responsibilities = item.get("responsibilities")
        perks = item.get("perks")

        def _append_section(heading: str, content: Any) -> None:
            if not content:
                return
            sections.append(f"<h2>{heading}</h2>")
            if isinstance(content, list):
                bullets = "".join(f"<li>{_stringify(entry)}</li>" for entry in content if entry)
                if bullets:
                    sections.append(f"<ul>{bullets}</ul>")
            else:
                sections.append(f"<p>{_stringify(content)}</p>")

        _append_section("Overview", overview)
        _append_section("Key Qualifications", qualifications)
        _append_section("Responsibilities", responsibilities)
        _append_section("Perks", perks)

        metadata_pairs = [
            (key, value)
            for key, value in item.items()
            if isinstance(key, str)
            and key.lower()
            not in {
                "company",
                "organization",
                "title",
                "location",
                "locations",
                "url",
                "description",
                "notes",
                "about",
                "summary",
                "qualifications",
                "requirements",
                "responsibilities",
                "perks",
            }
        ]

        if metadata_pairs:
            sections.append("<h2>Additional Details</h2>")
            sections.append("<ul>")
            for key, value in metadata_pairs:
                sections.append(f"<li><strong>{key.capitalize()}:</strong> {_stringify(value)}</li>")
            sections.append("</ul>")

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
