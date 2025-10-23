from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


_CACHED_CONFIG: dict[str, Any] | None = None


def _defaults() -> dict[str, Any]:
    return {
        "titles": {
            "match_any": [
                "software engineer",
                "swe",
                "backend",
                "back-end",
                "full stack",
                "full-stack",
            ]
        },
        "new_grad": {
            "phrases": [
                "new grad",
                "entry level",
                "university",
                "graduate",
            ]
        },
        "weights": {
            "title_match": 0.15,
            "new_grad": 0.20,
            "geo_core": 0.10,
            "geo_remote_tie": 0.05,
        },
        "skills": {
            "strong": {
                "Python": 0.08,
                "Java": 0.07,
                "C++": 0.06,
                "Swift": 0.05,
                "SQL": 0.05,
                "MySQL": 0.05,
                "PostgreSQL": 0.05,
                "Postgres": 0.05,
                "MongoDB": 0.04,
            },
            "web_ops": {
                "HTML": 0.03,
                "CSS": 0.03,
                "Linux": 0.03,
                "Power BI": 0.03,
            },
            "ai_tools": {
                "LangChain": 0.05,
                "NumPy": 0.04,
                "SQLAlchemy": 0.04,
                "Streamlit": 0.04,
                "CrewAI": 0.04,
                "Ollama": 0.04,
            },
            "ai_group": {
                "terms": [
                    "Agentic AI",
                    "AI Agent",
                    "RAG",
                    "retrieval-augmented",
                    "LLM fine-tuning",
                    "multimodal",
                ],
                "cap": 0.10,
            },
        },
        "geos": {
            "core": {
                "NYC": ["new york", "nyc"],
                "Seattle": ["seattle"],
                "SF Bay": [
                    "san francisco",
                    "sf",
                    "san jose",
                    "sunnyvale",
                    "mountain view",
                    "palo alto",
                    "redwood city",
                    "oakland",
                    "berkeley",
                    "bay area",
                ],
                "LA": ["los angeles", "la"],
                "Irvine/OC": ["irvine", "orange county", "oc"],
            },
            "remote_tie_terms": ["remote", "hybrid"],
        },
        "rationale": {"max_tags": 4},
    }


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except Exception:
        return {}

    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def get_ranking_config() -> dict[str, Any]:
    global _CACHED_CONFIG
    if _CACHED_CONFIG is not None:
        return _CACHED_CONFIG

    defaults = _defaults()

    # Look for agentic_jobs/config/rank.yaml relative to this file
    # package root: agentic_jobs/
    pkg_root = Path(__file__).resolve().parents[2]
    config_path = pkg_root / "config" / "rank.yaml"

    if config_path.exists():
        loaded = _load_yaml(config_path)
        if isinstance(loaded, dict):
            _CACHED_CONFIG = _deep_merge(defaults, loaded)
        else:
            _CACHED_CONFIG = defaults
    else:
        _CACHED_CONFIG = defaults

    return _CACHED_CONFIG


