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
    4. The path matches at least one safe pattern, OR the domain is a known aggregator
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

    # Known aggregators are always safe (they have their own ToS and structure)
    if any(netloc == agg or netloc.endswith("." + agg) for agg in _AGGREGATOR_DOMAINS):
        return True

    # For all other domains (company sites), require a matching safe path pattern
    return any(pattern.search(url) for pattern in _SAFE_PATH_PATTERNS)


def build_research_urls(company_name: str, company_domain: str) -> list[str]:
    """
    Build a conservative list of candidate URLs to research for a company.
    All URLs are constructed from known patterns — never from user input directly.
    Returns at most 4 URLs to keep the scrape focused.
    """
    urls: list[str] = []
    domain = company_domain.lower().strip().lstrip("www.")

    if not domain:
        return []

    # Company's own informational pages
    base = f"https://{domain}"
    candidates = [
        f"{base}/about",
        f"{base}/company",
        f"{base}/careers",
        f"{base}/products",
    ]
    for url in candidates:
        if is_safe_url(url):
            urls.append(url)

    # LinkedIn company page (constructed from name, not arbitrary input)
    slug = _to_slug(company_name)
    if slug:
        li_url = f"https://www.linkedin.com/company/{slug}"
        if is_safe_url(li_url):
            urls.append(li_url)

    # Cap at 4 total pages — we don't need more for a research brief
    return urls[:4]


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
