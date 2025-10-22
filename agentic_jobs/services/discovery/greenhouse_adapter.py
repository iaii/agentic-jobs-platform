from __future__ import annotations

import html
import json
import logging
import re
from typing import Any, Dict, Sequence
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

import httpx

from agentic_jobs.config import Settings
from agentic_jobs.core.enums import JobSourceType, SubmissionMode
from agentic_jobs.services.discovery.base import (
    DiscoveryError,
    JobDetail,
    JobRef,
    RobotsDisallowedError,
    SourceAdapter,
)
from agentic_jobs.services.discovery.rate_limiter import AsyncRateLimiter


LOGGER = logging.getLogger(__name__)

OPENING_BLOCK_RE = re.compile(r'<div[^>]*class="[^"]*opening[^"]*"[^>]*>(.*?)</div>', re.S | re.IGNORECASE)
ANCHOR_RE = re.compile(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.IGNORECASE)
LOCATION_RE = re.compile(r'<span[^>]*class="[^"]*location[^"]*"[^>]*>(.*?)</span>', re.S | re.IGNORECASE)
LD_JSON_RE = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.IGNORECASE)


class GreenhouseAdapter(SourceAdapter):
    source_name = "greenhouse"
    job_source_type = JobSourceType.GREENHOUSE
    submission_mode = SubmissionMode.ATS
    USER_AGENT = "AgenticJobsDiscoveryBot/0.1"

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        rate_limiter: AsyncRateLimiter | None = None,
    ) -> None:
        self.settings = settings
        self.base_url = settings.discovery_base_url.rstrip("/")
        self.sitemap_url = settings.discovery_sitemap_url
        self.allowed_domains = {domain.lower() for domain in settings.allowed_domains_list}
        timeout = httpx.Timeout(settings.request_timeout_seconds)
        self._client = client or httpx.AsyncClient(timeout=timeout, headers={"User-Agent": self.USER_AGENT})
        self._owns_client = client is None
        self._limiter = rate_limiter or AsyncRateLimiter(settings.requests_per_minute, 60.0)
        self._robots: RobotFileParser | None = None
        self._robots_checked = False
        self._board_meta: Dict[str, Dict[str, Any]] = {}

    async def __aenter__(self) -> "GreenhouseAdapter":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def discover(self) -> Sequence[str]:
        return await self.discover_from_sitemap()

    async def discover_from_sitemap(self) -> Sequence[str]:
        response = await self._request(self.sitemap_url)
        try:
            document = ElementTree.fromstring(response.text)
        except ElementTree.ParseError as exc:
            raise DiscoveryError("Unable to parse Greenhouse sitemap") from exc

        slugs: set[str] = set()
        for loc in document.findall(".//{*}loc"):
            if loc.text:
                slug = self._extract_slug(loc.text)
                if slug:
                    slugs.add(slug)
        return sorted(slugs)

    async def list_jobs(self, org_slug: str) -> Sequence[JobRef]:
        json_url = f"{self.base_url}/{org_slug}/embed/job_board/json"
        try:
            payload = await self._request_json(json_url)
            self._board_meta[org_slug] = payload.get("meta", {})
            return self._parse_jobs_from_json(org_slug, payload)
        except RobotsDisallowedError:
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in {403, 404, 500}:
                raise
            LOGGER.debug("JSON feed unavailable for %s (%s)", org_slug, exc.response.status_code)
        except (ValueError, json.JSONDecodeError) as exc:
            LOGGER.debug("Invalid JSON for %s feed: %s", org_slug, exc)

        html_url = f"{self.base_url}/{org_slug}"
        html = await self._request_text(html_url)
        return self._parse_jobs_from_html(org_slug, html, html_url)

    async def fetch_job_detail(self, job_ref: JobRef) -> JobDetail:
        html = await self._request_text(job_ref.detail_url)
        company_name = self._company_from_ref(job_ref)
        detail_metadata: dict[str, Any] = {}

        ld_json = self._parse_ld_json(html)
        if ld_json:
            detail_metadata["ld_json"] = ld_json
            organization = ld_json.get("hiringOrganization")
            if isinstance(organization, dict):
                company = organization.get("name")
                if isinstance(company, str) and company.strip():
                    company_name = company.strip()

        if not company_name:
            company_name = self._slug_to_company(job_ref.org_slug)

        return JobDetail(
            job_ref=job_ref,
            html=html,
            company_name=company_name,
            metadata=detail_metadata,
        )

    def canonical_id(self, job_ref: JobRef) -> str:
        return f"GH:{job_ref.job_id}"

    # Internal helpers ----------------------------------------------------- #

    async def _ensure_robots(self) -> None:
        if self._robots_checked:
            return

        robots_url = urljoin(self.base_url + "/", "robots.txt")
        try:
            async with self._limiter:
                response = await self._client.get(robots_url)
            response.raise_for_status()
            parser = RobotFileParser()
            parser.parse(response.text.splitlines())
            self._robots = parser
        except Exception as exc:  # noqa: BLE001 - treat failures as allow-all
            LOGGER.debug("Failed to load robots.txt (%s): %s", robots_url, exc)
            self._robots = None
        finally:
            self._robots_checked = True

    def _is_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            return False
        if parsed.netloc.lower() not in self.allowed_domains:
            return False
        if not self._robots:
            return True
        return self._robots.can_fetch(self.USER_AGENT, parsed.path)

    async def _request(self, url: str) -> httpx.Response:
        await self._ensure_robots()
        if not self._is_allowed(url):
            raise RobotsDisallowedError(f"robots.txt disallows fetching {url}")

        async with self._limiter:
            response = await self._client.get(url)
        response.raise_for_status()
        return response

    async def _request_text(self, url: str) -> str:
        response = await self._request(url)
        return response.text

    async def _request_json(self, url: str) -> dict[str, Any]:
        response = await self._request(url)
        return response.json()

    def _extract_slug(self, url: str) -> str | None:
        parsed = urlparse(url)
        if parsed.netloc.lower() not in self.allowed_domains:
            return None
        parts = [segment for segment in parsed.path.split("/") if segment]
        if not parts:
            return None
        slug = parts[0]
        if slug in {"embed", "sitemap"}:
            return None
        return slug

    def _parse_jobs_from_json(self, org_slug: str, payload: dict[str, Any]) -> Sequence[JobRef]:
        jobs: list[JobRef] = []
        departments = payload.get("departments", [])
        for department in departments:
            department_name = department.get("name")
            for job in department.get("jobs", []):
                job_id = str(job.get("id") or job.get("internal_job_id"))
                if not job_id:
                    continue
                absolute_url = job.get("absolute_url")
                if not isinstance(absolute_url, str):
                    continue
                location = self._resolve_location(job)
                title = job.get("title") or "Untitled role"
                jobs.append(
                    JobRef(
                        source=self.source_name,
                        org_slug=org_slug,
                        job_id=job_id,
                        title=title,
                        location=location,
                        detail_url=absolute_url,
                        metadata={"department": department_name, "job": job},
                    )
                )
        return jobs

    def _parse_jobs_from_html(self, org_slug: str, html_doc: str, board_url: str) -> Sequence[JobRef]:
        jobs: list[JobRef] = []
        for match in OPENING_BLOCK_RE.finditer(html_doc):
            block = match.group(1)
            anchor_match = ANCHOR_RE.search(block)
            if not anchor_match:
                continue
            href = anchor_match.group(1)
            title_html = anchor_match.group(2)
            title = self._clean_html_fragment(title_html) or "Untitled role"
            location_match = LOCATION_RE.search(block)
            location = "Unknown"
            if location_match:
                location_text = self._clean_html_fragment(location_match.group(1))
                if location_text:
                    location = location_text

            absolute_url = urljoin(board_url, href) if href.startswith("/") else href
            job_id = self._job_id_from_url(absolute_url)
            if not job_id:
                continue

            jobs.append(
                JobRef(
                    source=self.source_name,
                    org_slug=org_slug,
                    job_id=job_id,
                    title=title,
                    location=location,
                    detail_url=absolute_url,
                    metadata={"source": "html_board"},
                )
            )
        return jobs

    def _job_id_from_url(self, url: str) -> str | None:
        parsed = urlparse(url)
        parts = [segment for segment in parsed.path.split("/") if segment]
        if not parts:
            return None
        if len(parts) >= 2 and parts[-2] == "jobs":
            return parts[-1]
        if parts[-1].isdigit():
            return parts[-1]
        return None

    def _resolve_location(self, job: dict[str, Any]) -> str:
        location = job.get("location")
        if isinstance(location, dict):
            name = location.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
        if isinstance(location, str) and location.strip():
            return location.strip()
        return "Unknown"

    def _parse_ld_json(self, html_doc: str) -> dict[str, Any] | None:
        for match in LD_JSON_RE.finditer(html_doc):
            raw = html.unescape(match.group(1))
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("@type") == "JobPosting":
                return data
        return None

    def _company_from_ref(self, job_ref: JobRef) -> str | None:
        job_meta = job_ref.metadata.get("job", {})
        if isinstance(job_meta, dict):
            company = job_meta.get("company")
            if isinstance(company, str) and company.strip():
                return company.strip()

        board_meta = self._board_meta.get(job_ref.org_slug, {})
        if isinstance(board_meta, dict):
            title = board_meta.get("title")
            if isinstance(title, str) and title.strip():
                fragments = [fragment.strip() for fragment in title.split("-") if fragment.strip()]
                if fragments:
                    return fragments[0]
        return None

    def _clean_html_fragment(self, fragment: str) -> str:
        text = re.sub(r"<[^>]+>", "", fragment)
        text = html.unescape(text)
        return text.strip()

    def _slug_to_company(self, slug: str) -> str:
        parts = slug.replace("_", "-").split("-")
        return " ".join(part.capitalize() for part in parts if part)
