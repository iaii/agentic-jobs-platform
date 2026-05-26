from __future__ import annotations

"""
CompanyScraper — safe, rate-limited web scraping for company research.

Safety measures baked in:
  1. URL allowlist/blocklist validation (domains.py) before every request
  2. Robots.txt compliance — fetches and caches robots.txt per domain, skips
     disallowed paths (uses urllib.robotparser, same as GreenhouseAdapter)
  3. Per-domain rate limiting — separate AsyncRateLimiter per domain so a
     single company's site is hit at most `scraper_rate_limit` req/10s
  4. Global concurrency cap — semaphore limits total simultaneous fetches
  5. Hard timeout per request (default: 10s from settings)
  6. Max response body size — reading stops at 500KB to avoid memory issues
     from accidentally fetching a large binary/PDF
  7. Content-type guard — only processes text/html responses
  8. Identifying User-Agent — honest bot identifier, not spoofing a browser
  9. No JavaScript execution — pure HTTP requests only (no Playwright/Selenium)
 10. Exception isolation — a failure on one URL never crashes the whole batch

KNOWN LIMITATION — bot-detection:
  Sites that deploy aggressive bot detection (LinkedIn, Glassdoor, Crunchbase)
  will return 403s or client-side-rendered shells with no useful text content.
  These cannot be scraped reliably without a JS-capable browser engine
  (Playwright, Puppeteer, etc.). Until that is added, those domains are
  allowlisted in domains.py but intentionally not generated as research targets.

Prompt Improver integration point (future):
  After scraping, the raw text in ScrapedPage.text can optionally be passed
  through a prompt improvement step before the ResearcherAgent LLM call.
  The natural place is in PipelineCoordinator.run(), after calling
  CompanyScraper.scrape() but before building the researcher's user message.
  Example:
      raw_pages = await scraper.scrape(urls)
      # Future: improved_notes = await improve_scraped_context(raw_pages)
      research_brief = await researcher.run(company_pages=raw_pages, ...)
"""

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from agentic_jobs.config import settings
from agentic_jobs.services.discovery.rate_limiter import AsyncRateLimiter
from agentic_jobs.services.research.domains import is_safe_url
import asyncio


LOGGER = logging.getLogger(__name__)

USER_AGENT = "AgenticJobsResearchBot/0.1 (company research; not for indexing)"


@dataclass(slots=True)
class ScrapedPage:
    url: str
    title: str
    text: str       # Cleaned plain text, truncated to SCRAPER_MAX_TEXT_CHARS
    status_code: int
    error: str = ""


class CompanyScraper:
    """
    Fetches and extracts plain text from company web pages.
    Safe by default: validates every URL, checks robots.txt, rate-limits per domain.
    """

    def __init__(self) -> None:
        # Per-domain rate limiters — lazily created, shared across scrape() calls
        self._domain_limiters: dict[str, AsyncRateLimiter] = defaultdict(
            lambda: AsyncRateLimiter(
                max_calls=settings.scraper_rate_limit,
                period=10.0,  # N requests per 10 seconds per domain
            )
        )
        # Robots.txt cache: domain → (RobotFileParser, fetched_at timestamp)
        self._robots_cache: dict[str, tuple[RobotFileParser, float]] = {}
        self._global_sem = asyncio.Semaphore(settings.scraper_global_concurrency)

    async def scrape(
        self,
        urls: list[str],
        *,
        timeout: float | None = None,
    ) -> list[ScrapedPage]:
        """
        Fetch and extract text from a list of URLs.
        Invalid or blocked URLs are skipped silently (logged at DEBUG).
        Each URL is fetched independently; failures don't affect the batch.
        """
        t = timeout or float(settings.scraper_timeout_seconds)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(t),
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            max_redirects=3,
        ) as client:
            tasks = [self._fetch_one(url, client) for url in urls]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        pages: list[ScrapedPage] = []
        for url, result in zip(urls, results):
            if isinstance(result, Exception):
                LOGGER.warning("Scraper unexpected error for %s: %s", url, result)
            elif result is not None:
                pages.append(result)
        return pages

    async def _fetch_one(self, url: str, client: httpx.AsyncClient) -> ScrapedPage | None:
        # --- Safety gate 1: URL allowlist validation ---
        if not is_safe_url(url):
            LOGGER.debug("Scraper: skipping disallowed URL %s", url)
            return None

        domain = urlparse(url).netloc.lower()

        # --- Safety gate 2: robots.txt compliance ---
        if not await self._robots_allowed(url, client):
            LOGGER.info("Scraper: robots.txt disallows %s", url)
            return None

        # --- Safety gate 3: per-domain rate limiting ---
        async with self._domain_limiters[domain]:
            # --- Safety gate 4: global concurrency cap ---
            async with self._global_sem:
                return await self._do_fetch(url, client)

    async def _do_fetch(self, url: str, client: httpx.AsyncClient) -> ScrapedPage | None:
        try:
            async with client.stream("GET", url) as response:
                # --- Safety gate 5: content-type guard ---
                content_type = response.headers.get("content-type", "")
                if "text/html" not in content_type:
                    LOGGER.debug("Scraper: non-HTML content-type at %s (%s)", url, content_type)
                    return None

                # --- Safety gate 6: max body size ---
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= settings.scraper_max_body_bytes:
                        LOGGER.debug("Scraper: body size limit reached for %s", url)
                        break

                html = b"".join(chunks).decode("utf-8", errors="replace")

            title, text = self._extract_text(html, source_url=url)
            return ScrapedPage(
                url=url,
                title=title,
                text=text,
                status_code=response.status_code,
            )

        except httpx.TimeoutException:
            LOGGER.info("Scraper: timeout fetching %s", url)
            return ScrapedPage(url=url, title="", text="", status_code=0, error="timeout")
        except httpx.RequestError as exc:
            LOGGER.info("Scraper: request error for %s: %s", url, exc)
            return ScrapedPage(url=url, title="", text="", status_code=0, error=str(exc))
        except httpx.HTTPStatusError as exc:
            LOGGER.info("Scraper: HTTP %d for %s", exc.response.status_code, url)
            return ScrapedPage(url=url, title="", text="", status_code=exc.response.status_code, error="http_error")

    async def _robots_allowed(self, url: str, client: httpx.AsyncClient) -> bool:
        """Check robots.txt for the given URL. Defaults to allowed on any fetch error."""
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

        import time
        now = time.monotonic()
        cached = self._robots_cache.get(domain)

        if cached is None or (now - cached[1]) > settings.scraper_robots_ttl_seconds:
            rp = RobotFileParser()
            rp.set_url(robots_url)
            try:
                async with self._global_sem:
                    resp = await client.get(robots_url, timeout=float(settings.request_timeout_seconds))
                    if resp.status_code == 200:
                        rp.parse(resp.text.splitlines())
                    else:
                        # No robots.txt or error → assume allowed
                        return True
            except Exception:
                # Network error fetching robots.txt → assume allowed
                return True
            self._robots_cache[domain] = (rp, now)
        else:
            rp = cached[0]

        return rp.can_fetch(USER_AGENT, url)

    @staticmethod
    def _extract_text(html: str, *, source_url: str = "") -> tuple[str, str]:
        """
        Extract clean plain text from HTML using BeautifulSoup.
        Removes navigation, footers, headers, scripts, styles, and ads.
        Returns (title, body_text).
        """
        soup = BeautifulSoup(html, "html.parser")

        # Extract title
        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else ""

        # Remove noise elements
        for tag in soup(["script", "style", "nav", "footer", "header",
                         "aside", "form", "iframe", "noscript",
                         "meta", "link", "button", "input"]):
            tag.decompose()

        # Also remove common ad/cookie/promo divs by class/id patterns
        _NOISE_PATTERN = re.compile(
            r"(cookie|banner|popup|modal|overlay|newsletter|subscribe|promo|ad-|advertisement)",
            re.IGNORECASE,
        )
        for tag in soup.find_all(True):
            classes = " ".join(tag.get("class", []))
            id_attr = tag.get("id", "")
            if _NOISE_PATTERN.search(classes) or _NOISE_PATTERN.search(id_attr):
                tag.decompose()

        # Prefer main content areas if available
        main = soup.find("main") or soup.find(id="main") or soup.find(id="content") or soup.body
        if main is None:
            return title, ""

        # Collapse whitespace
        raw = main.get_text(separator="\n")
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        # Remove very short noise lines (single words, stray punctuation)
        lines = [line for line in lines if len(line) > 15]
        text = "\n".join(lines)

        from agentic_jobs.services.agents.guardrails import sanitize
        text = sanitize(text[:settings.scraper_max_text_chars], source=f"scrape:{source_url}")
        return title, text
