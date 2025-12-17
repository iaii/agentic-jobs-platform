from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


DEFAULT_INCLUDE = [
    "software engineer",
    "software engineering",
    "swe",
    "new grad",
    "engineer i",
    "engineering i",
    "engineer 1",
    "engineering 1",
]

DEFAULT_EXCLUDE = [
    "manager",
    "director",
    "lead",
    "principal",
    "architect",
    "staff",
    "sr",
    "senior",
]

DEFAULT_ADAPTERS = {
    "greenhouse": True,
    "simplify": True,
    "newgrad2026": True,
}


@dataclass(slots=True)
class JobFilterConfig:
    include_keywords: list[str]
    exclude_keywords: list[str]
    adapters: dict[str, bool]


def _normalize_list(values: Any, fallback: list[str]) -> list[str]:
    if not isinstance(values, list):
        return list(fallback)
    cleaned: list[str] = []
    for item in values:
        if not isinstance(item, str):
            continue
        item_clean = item.strip().lower()
        if item_clean:
            cleaned.append(item_clean)
    return cleaned or list(fallback)


def _normalize_adapters(values: Any) -> dict[str, bool]:
    adapters = dict(DEFAULT_ADAPTERS)
    if isinstance(values, dict):
        for key, val in values.items():
            if isinstance(val, bool):
                adapters[key.lower()] = val
    return adapters


def _load_raw_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fp:
            return yaml.safe_load(fp) or {}
    except Exception:
        return {}


@lru_cache()
def get_job_filter_config(path: str | None) -> JobFilterConfig:
    cfg_path = Path(path or "config/job_filters.yaml")
    raw = _load_raw_config(cfg_path)
    filters_raw = raw.get("filters", {}) if isinstance(raw, dict) else {}
    include_keywords = _normalize_list(filters_raw.get("include_keywords"), DEFAULT_INCLUDE)
    exclude_keywords = _normalize_list(filters_raw.get("exclude_keywords"), DEFAULT_EXCLUDE)
    adapters = _normalize_adapters(raw.get("adapters")) if isinstance(raw, dict) else dict(DEFAULT_ADAPTERS)
    return JobFilterConfig(include_keywords=include_keywords, exclude_keywords=exclude_keywords, adapters=adapters)
