from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

from agentic_jobs.db import models

from .config import get_ranking_config
from .rationale import compose_rationale


TITLE_KEYWORDS = {
    "software engineer",
    "backend",
    "back-end",
    "full stack",
    "full-stack",
    "swe",
}

NEW_GRAD_KEYWORDS = {
    "new grad",
    "entry level",
    "university grad",
    "graduate",
}

GEO_KEYWORDS = {
    "new york",
    "nyc",
    "seattle",
    "san francisco",
    "san jose",
    "sunnyvale",
    "mountain view",
    "palo alto",
    "redwood city",
    "oakland",
    "berkeley",
    "los angeles",
    "la ",
    "irvine",
    "orange county",
}


@dataclass(slots=True)
class ScoreResult:
    score: float
    rationale: str
    features: Dict[str, float] = field(default_factory=dict)


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(value, maximum))


def score_job(job: models.Job) -> ScoreResult:
    """Deterministic scorer using config-driven weights and keyword lists.

    Returns a score in [0,1], a concise rationale string, and a feature→weight map
    of applied contributions.
    """
    cfg = get_ranking_config()

    title_lc = (job.title or "").lower()
    jd_text_lc = (job.jd_text or "").lower()
    location_lc = (job.location or "").lower()

    total = 0.0
    features: dict[str, float] = {}

    # Title match
    matched_title_tag: str | None = None
    for raw_kw in cfg["titles"]["match_any"]:
        if raw_kw in title_lc:
            # Map variants to canonical tags for rationale display
            if raw_kw in {"backend", "back-end"}:
                matched_title_tag = "backend"
            elif raw_kw in {"full stack", "full-stack"}:
                matched_title_tag = "full stack"
            elif raw_kw in {"software engineer", "swe"}:
                matched_title_tag = "software engineer"
            else:
                matched_title_tag = raw_kw
            delta = float(cfg["weights"]["title_match"])
            total += delta
            features[f"title:{matched_title_tag}"] = delta
            break

    # New-grad phrases
    if any(phrase in jd_text_lc for phrase in cfg["new_grad"]["phrases"]):
        delta = float(cfg["weights"]["new_grad"]) 
        total += delta
        features["new_grad"] = delta

    # Skills (case-insensitive, avoid double counting)
    def _apply_skill_bucket(bucket: dict[str, float]) -> None:
        nonlocal total
        for skill_name, weight in bucket.items():
            if skill_name.lower() in jd_text_lc:
                key = f"skill:{skill_name}"
                if key not in features:
                    w = float(weight)
                    features[key] = w
                    total += w

    skills_cfg = cfg["skills"]
    for bucket_name in ("strong", "web_ops", "ai_tools"):
        _apply_skill_bucket(skills_cfg.get(bucket_name, {}))

    # AI group (capped)
    ai_terms = [t.lower() for t in skills_cfg["ai_group"]["terms"]]
    ai_term_matches = sum(1 for t in ai_terms if t in jd_text_lc)
    if ai_term_matches:
        per_term = 0.05  # fixed as per spec
        cap = float(skills_cfg["ai_group"].get("cap", 0.10))
        applied = min(ai_term_matches * per_term, cap)
        if applied > 0:
            features["ai_group"] = applied
            total += applied

    # Geo core regions
    matched_regions: list[str] = []
    for region, terms in cfg["geos"]["core"].items():
        if any(term in location_lc for term in terms):
            matched_regions.append(region)
            delta = float(cfg["weights"]["geo_core"]) 
            features[f"geo:{region}"] = delta
            total += delta

    # Remote/hybrid tie boost only if tied to a matched region
    remote_terms = [t.lower() for t in cfg["geos"]["remote_tie_terms"]]
    if matched_regions and any(t in location_lc for t in remote_terms):
        # annotate each matched region with tie (but only count once overall per spec)
        # We apply the tie once for the first matched region to avoid overweighting.
        region = matched_regions[0]
        delta = float(cfg["weights"]["geo_remote_tie"]) 
        features[f"geo_tie:{region}"] = delta
        total += delta

    final_score = round(_clamp(total), 2)
    rationale = compose_rationale(features, job, cfg)
    return ScoreResult(score=final_score, rationale=rationale, features=features)
