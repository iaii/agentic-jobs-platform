from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from agentic_jobs.services.discovery.base import DiscoveryError

WORKDAY_CXS_RE = re.compile(r"https://(?P<host>[\w\.-]+)/wday/cxs/(?P<tenant>[^/]+)/(?P<site>[^/]+)/", re.IGNORECASE)
LEVER_API_RE = re.compile(r"https://api\.lever\.co/v0/postings/(?P<company>[\w\-_]+)", re.IGNORECASE)


class ParserDetectionError(DiscoveryError):
    """Raised when auto-detection cannot determine an ATS parser."""


@dataclass(slots=True)
class DetectionResult:
    parser: str
    options: dict[str, Any]


class ParserDetector:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def detect(self, site_url: str) -> DetectionResult:
        url = site_url.strip()
        if not url:
            raise ParserDetectionError("Site URL is required for parser detection.")
        heuristics = self._infer_from_url(url)
        if heuristics:
            return heuristics
        body = await self._fetch_body(url)
        heuristics = self._infer_from_body(body)
        if heuristics:
            return heuristics
        raise ParserDetectionError(f"Unable to detect ATS parser for {url}")

    def _infer_from_url(self, site_url: str) -> DetectionResult | None:
        parsed = urlparse(site_url)
        host = (parsed.netloc or "").lower()
        path = parsed.path or ""
        if host.endswith("lever.co"):
            parts = [segment for segment in path.split("/") if segment]
            if parts:
                return DetectionResult(parser="lever", options={"company": parts[0]})
        if host.endswith("myworkdayjobs.com") or "workday" in host or "/wday/" in path:
            tenant, site = self._extract_workday_from_path(path)
            if tenant and site:
                return DetectionResult(
                    parser="workday",
                    options={
                        "host": host or parsed.hostname or "",
                        "tenant": tenant,
                        "site": site,
                    },
                )
        return None

    async def _fetch_body(self, site_url: str) -> str:
        try:
            response = await self._client.get(site_url, timeout=30.0)
            response.raise_for_status()
            return response.text
        except httpx.HTTPError as exc:
            raise ParserDetectionError(f"Failed to fetch {site_url} for detection") from exc

    def _infer_from_body(self, body: str) -> DetectionResult | None:
        workday_match = WORKDAY_CXS_RE.search(body)
        if workday_match:
            return DetectionResult(
                parser="workday",
                options={
                    "host": workday_match.group("host"),
                    "tenant": workday_match.group("tenant"),
                    "site": workday_match.group("site"),
                },
            )
        lever_match = LEVER_API_RE.search(body)
        if lever_match:
            return DetectionResult(parser="lever", options={"company": lever_match.group("company")})
        return None

    def _extract_workday_from_path(self, path: str) -> tuple[str | None, str | None]:
        fragments = [segment for segment in path.split("/") if segment]
        if len(fragments) >= 2:
            return fragments[0], fragments[1]
        return None, None
