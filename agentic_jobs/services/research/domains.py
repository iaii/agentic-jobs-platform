from __future__ import annotations

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
