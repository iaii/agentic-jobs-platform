from __future__ import annotations

import json as _json
import re
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Allowlist of URL path patterns we consider safe to scrape.
# The scraper only fetches URLs that match one of these patterns AND come
# from the company's own domain or a known aggregator.
# ---------------------------------------------------------------------------

# Regex patterns matched against the full URL (case-insensitive).
# These target informational pages, not forms, auth flows, or user data.
_SAFE_PATH_PATTERNS: list[re.Pattern] = [re.compile(p, re.IGNORECASE) for p in [
    r"/about",
    r"/company",
    r"/culture",
    r"/mission",
    r"/vision",
    r"/values",
    r"/team",
    r"/products?",
    r"/platform",
    r"/solutions?",
    r"/careers?",
    r"/jobs",
    r"/blog",
    r"/press",
    r"/news",
    r"/engineering",
    r"/technology",
    r"/overview",       # Glassdoor Overview pages
    r"/organization",   # Crunchbase organization pages
]]

# Known aggregator domains we explicitly allow (exact match on netloc).
# These bypass the safe-path pattern check because their URL structure differs
# from company sites (e.g. /overview on Glassdoor, /organization on Crunchbase).
#
# KNOWN LIMITATION: LinkedIn, Glassdoor, and Crunchbase deploy aggressive bot
# detection that blocks plain HTTP scrapers. In practice, requests to these
# domains return 403s or JS-rendered shells with no useful content. They are
# listed here for correctness (we are not scraping them for malicious reasons)
# but build_research_urls does not generate URLs for them. If JS-capable
# scraping is added in the future, these are the first candidates to enable.
_AGGREGATOR_DOMAINS: frozenset[str] = frozenset([
    "linkedin.com",
    "glassdoor.com",
    "crunchbase.com",
    "builtin.com",
    "levels.fyi",
    "teamblind.com",
])

# Domains that are never scraped regardless of path.
_BLOCKED_DOMAINS: frozenset[str] = frozenset([
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "tiktok.com",
    "reddit.com",
    "youtube.com",
    "wikipedia.org",
    "yelp.com",
    "indeed.com",   # scraping Indeed violates their ToS
    "ziprecruiter.com",
])

# Never scrape these path segments — they indicate auth/forms/user data.
_BLOCKED_PATH_SEGMENTS: frozenset[str] = frozenset([
    "login", "signin", "signup", "register", "auth",
    "checkout", "cart", "payment", "billing",
    "admin", "dashboard", "api", "graphql",
    "download", "uploads", "static", "assets",
    "cdn", ".pdf", ".zip", ".exe", ".dmg",
])


def is_safe_url(url: str) -> bool:
    """
    Returns True only if:
    1. The URL uses https
    2. The domain is not blocked
    3. The path doesn't contain blocked segments (auth, forms, API endpoints, etc.)
    4. The path is empty/root (homepage), OR matches a safe pattern, OR the domain
       is a known aggregator
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    # Must be HTTPS
    if parsed.scheme != "https":
        return False

    netloc = parsed.netloc.lower().lstrip("www.")
    path = parsed.path.lower()

    # Hard block list
    if any(netloc == blocked or netloc.endswith("." + blocked) for blocked in _BLOCKED_DOMAINS):
        return False

    # Block dangerous path segments
    path_parts = set(path.replace("/", " ").replace("-", " ").replace("_", " ").split())
    if path_parts & _BLOCKED_PATH_SEGMENTS:
        return False

    # Company homepage (empty path or "/") — always safe if domain is not blocked
    if not path or path == "/":
        return True

    # Known aggregators are always safe (they have their own ToS and structure)
    if any(netloc == agg or netloc.endswith("." + agg) for agg in _AGGREGATOR_DOMAINS):
        return True

    # For all other domains (company sites), require a matching safe path pattern
    return any(pattern.search(url) for pattern in _SAFE_PATH_PATTERNS)


def build_research_urls(company_name: str, company_domain: str) -> list[str]:
    """
    Build a conservative list of candidate URLs to research for a company.
    All URLs are constructed from known patterns — never from user input directly.

    Ordering is intentional: homepage first (most reliably present), then
    progressively more specific informational pages. The scraper fetches all
    concurrently, but the researcher reads pages in list order — pages that
    appear first get priority within the context budget.

    Returns at most 5 URLs to keep the scrape focused.
    """
    urls: list[str] = []
    domain = company_domain.lower().strip().lstrip("www.")

    if not domain:
        return []

    base = f"https://{domain}"
    candidates = [
        base,                       # homepage — most reliably present
        f"{base}/about",
        f"{base}/company",
        f"{base}/engineering",
        f"{base}/technology",
        f"{base}/careers",
    ]
    for url in candidates:
        if is_safe_url(url):
            urls.append(url)

    # Cap at 5 total pages
    return urls[:5]


def extract_domain(url: str) -> str:
    """Extract the bare domain (without www.) from a URL."""
    try:
        netloc = urlparse(url).netloc.lower()
        return netloc.lstrip("www.")
    except Exception:
        return ""


def _to_slug(name: str) -> str:
    """Convert a company name to a URL-safe slug."""
    slug = re.sub(r"[^a-z0-9\s-]", "", name.lower())
    slug = re.sub(r"[\s]+", "-", slug.strip())
    return slug[:60]  # LinkedIn slugs are capped


# ---------------------------------------------------------------------------
# Company website extraction from job page HTML
# ---------------------------------------------------------------------------

# Third-party ATS platforms — the job URL domain is not the company's site.
_ATS_EXACT_NETLOCS: frozenset[str] = frozenset([
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "boards.eu.greenhouse.io",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
    "apply.workable.com",
    "jobs.smartrecruiters.com",
    "jobs.jobvite.com",
    "app.dover.com",
    "recruiting.ultipro.com",
])

# Root domains whose subdomains are always ATS/HR platforms.
_ATS_ROOT_DOMAINS: frozenset[str] = frozenset([
    "myworkdayjobs.com",
    "icims.com",
    "taleo.net",
    "successfactors.com",
    "brassring.com",
    "oraclecloud.com",
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "workable.com",
    "smartrecruiters.com",
    "jobvite.com",
])

# Domains that are never useful as a company website — social, aggregators, etc.
_SKIP_DOMAINS: frozenset[str] = frozenset([
    "linkedin.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "glassdoor.com",
    "github.com",
    "indeed.com",
    "ziprecruiter.com",
    "workatastartup.com",
    "levels.fyi",
    "crunchbase.com",
    "builtin.com",
    "angel.co",
    "wellfound.com",
    "simplify.jobs",
    "teamblind.com",
])

# Subdomain prefixes that indicate a company's job/careers page rather than
# their main website.  Stripped iteratively so jobs.careers.microsoft.com → microsoft.com.
_JOB_SUBDOMAIN_PREFIXES: frozenset[str] = frozenset([
    "jobs", "job", "careers", "career", "work", "apply", "hiring",
    "talent", "hr", "recruit", "join", "opportunities", "boards",
])


def _root_domain(netloc: str) -> str:
    """Return the registrable domain (last two dot-separated parts)."""
    domain = netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    parts = domain.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain


def _is_third_party_domain(netloc: str) -> bool:
    """True if the netloc is an ATS platform, aggregator, or social site."""
    netloc = netloc.lower()
    if netloc in _ATS_EXACT_NETLOCS:
        return True
    root = _root_domain(netloc)
    return root in _ATS_ROOT_DOMAINS or root in _SKIP_DOMAINS


def _strip_job_subdomains(netloc: str) -> str:
    """
    Strip www. then iteratively remove known job-related subdomain prefixes.

    Examples:
      www.qualtrics.com          → qualtrics.com
      jobs.apple.com             → apple.com
      jobs.careers.microsoft.com → microsoft.com
      amazon.jobs                → amazon.jobs  (only 2 parts, left alone)
    """
    domain = netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    parts = domain.split(".")
    while len(parts) > 2 and parts[0] in _JOB_SUBDOMAIN_PREFIXES:
        parts = parts[1:]
    return ".".join(parts)


def _as_homepage(url: str) -> str | None:
    """Return https://<netloc> for a URL, or None if unparseable."""
    try:
        p = urlparse(url)
        if p.scheme in ("http", "https") and p.netloc:
            return f"https://{p.netloc.lower()}"
    except Exception:
        pass
    return None


def extract_company_website(html: str, job_url: str) -> str | None:
    """
    Extract the company's main website from a job posting page.

    Tries in priority order:
      1. LD+JSON hiringOrganization.url / sameAs
      2. <meta property="og:url">
      3. <link rel="canonical">
      4. External link scan — only on third-party ATS pages, only reads URLs
         that are explicitly present in the HTML (never guesses TLDs)
      5. Subdomain stripping — only on company-hosted pages
         (e.g. jobs.apple.com → apple.com; TLD is already known from the URL)

    Returns None if no confident match is found.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return None

    try:
        parsed_job = urlparse(job_url)
    except Exception:
        return None

    job_netloc = parsed_job.netloc.lower()
    job_is_third_party = _is_third_party_domain(job_netloc)

    soup = BeautifulSoup(html, "html.parser")

    # 1. LD+JSON
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = _json.loads(script.string or "")
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        org = data.get("hiringOrganization", {})
        if not isinstance(org, dict):
            continue
        for key in ("url", "sameAs"):
            val = org.get(key)
            if not isinstance(val, str) or not val.startswith("http"):
                continue
            home = _as_homepage(val)
            if home and not _is_third_party_domain(urlparse(home).netloc):
                return home

    # 2. OG meta
    og = soup.find("meta", property="og:url")
    if og:
        content = og.get("content") or ""
        if isinstance(content, str) and content.startswith("http"):
            home = _as_homepage(content)
            if home:
                netloc = urlparse(home).netloc.lower()
                if netloc != job_netloc and not _is_third_party_domain(netloc):
                    return home

    # 3. Canonical link
    canonical = soup.find("link", rel="canonical")
    if canonical:
        href = canonical.get("href") or ""
        if isinstance(href, str) and href.startswith("http"):
            home = _as_homepage(href)
            if home:
                netloc = urlparse(home).netloc.lower()
                if netloc != job_netloc and not _is_third_party_domain(netloc):
                    return home

    # 4. External link scan — ATS/third-party pages only.
    # We read URLs directly from href attributes; no TLD guessing.
    if job_is_third_party:
        # score: prefer shallower paths (homepage links have depth 0)
        candidates: list[tuple[int, str]] = []
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if not isinstance(href, str) or not href.startswith("http"):
                continue
            try:
                p = urlparse(href)
            except Exception:
                continue
            netloc = p.netloc.lower()
            if netloc == job_netloc or _is_third_party_domain(netloc):
                continue
            home = _as_homepage(href)
            if not home:
                continue
            depth = len([s for s in p.path.split("/") if s])
            candidates.append((depth, home))

        if candidates:
            candidates.sort(key=lambda x: x[0])
            # Return the shallowest-path unique domain
            seen: set[str] = set()
            for _, url in candidates:
                netloc = urlparse(url).netloc.lower()
                if netloc not in seen:
                    seen.add(netloc)
                    return url

    # 5. Subdomain stripping — only for company-hosted pages.
    # The TLD is already known from the URL, so this is safe.
    if not job_is_third_party:
        stripped = _strip_job_subdomains(job_netloc)
        return f"https://{stripped}"

    return None
