from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, List

import yaml


@dataclass(slots=True)
class UniversalFeedConfig:
    site_slug: str
    display_name: str
    feed_slug: str
    parser: str | None
    crawl_interval_minutes: int | None
    options: dict[str, Any]
    site_url: str | None = None

    @property
    def slug(self) -> str:
        return f"{self.site_slug}:{self.feed_slug}"

    @property
    def source_label(self) -> str:
        label = self.options.get("source_label")
        if isinstance(label, str) and label.strip():
            return label.strip()
        if self.feed_slug == "default":
            return self.display_name
        return f"{self.display_name} ({self.feed_slug})"

    @property
    def requires_detection(self) -> bool:
        return not self.parser or not self.options


@dataclass(slots=True)
class UniversalSitesConfig:
    feeds: list[UniversalFeedConfig]

    def feed_slugs(self) -> list[str]:
        return [feed.slug for feed in self.feeds]

    def get_feed(self, slug: str) -> UniversalFeedConfig | None:
        return next((feed for feed in self.feeds if feed.slug == slug), None)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        try:
            return yaml.safe_load(handle) or {}
        except yaml.YAMLError:
            return {}


def _normalize_sites(payload: Any) -> Iterable[UniversalFeedConfig]:
    if not isinstance(payload, dict):
        return []
    sites = payload.get("sites")
    if not isinstance(sites, list):
        return []

    feeds: List[UniversalFeedConfig] = []
    for site_entry in sites:
        if not isinstance(site_entry, dict):
            continue
        site_slug_raw = str(site_entry.get("site_slug") or "").strip().lower()
        if not site_slug_raw:
            continue
        display_name = str(site_entry.get("display_name") or site_slug_raw).strip() or site_slug_raw
        crawl_interval = site_entry.get("crawl_interval_minutes")
        if isinstance(crawl_interval, str) and crawl_interval.isdigit():
            crawl_interval_val: int | None = int(crawl_interval)
        elif isinstance(crawl_interval, (int, float)):
            crawl_interval_val = int(crawl_interval)
        else:
            crawl_interval_val = None
        feeds_raw = site_entry.get("feeds")
        if not isinstance(feeds_raw, list) or not feeds_raw:
            continue

        for idx, feed_entry in enumerate(feeds_raw):
            if not isinstance(feed_entry, dict):
                continue
            parser_raw = feed_entry.get("parser")
            parser = str(parser_raw).strip().lower() if isinstance(parser_raw, str) else None
            feed_slug_val = str(feed_entry.get("feed_slug") or "").strip().lower()
            if not feed_slug_val:
                feed_slug_val = "default" if len(feeds_raw) == 1 else f"feed{idx + 1}"
            options_raw = feed_entry.get("options")
            options = options_raw if isinstance(options_raw, dict) else {}
            site_url = feed_entry.get("site_url")
            site_url_val = str(site_url).strip() if isinstance(site_url, str) else None
            if not parser and not site_url_val:
                # Need at least parser or site_url to detect automatically
                continue
            feeds.append(
                UniversalFeedConfig(
                    site_slug=site_slug_raw,
                    display_name=display_name,
                    feed_slug=feed_slug_val,
                    parser=parser,
                    crawl_interval_minutes=crawl_interval_val,
                    options=options,
                    site_url=site_url_val,
                )
            )
    return feeds


@lru_cache()
def load_universal_sites_config(path: str | None) -> UniversalSitesConfig:
    cfg_path = Path(path or "config/universal_sites.yaml")
    payload = _load_yaml(cfg_path)
    feeds = list(_normalize_sites(payload))
    return UniversalSitesConfig(feeds=feeds)
