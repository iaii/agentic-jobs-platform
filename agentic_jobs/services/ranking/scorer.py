from __future__ import annotations

from dataclasses import dataclass

from agentic_jobs.db import models
from agentic_jobs.services.discovery.config import JobFilterConfig, get_job_filter_config


@dataclass(slots=True)
class ScoreResult:
    score: float
    rationale: str


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(value, maximum))


def score_job(job: models.Job, filter_config: JobFilterConfig | None = None) -> ScoreResult:
    """Deterministic placeholder scorer until MVPart 4 lands."""
    if filter_config is None:
        from agentic_jobs.config import settings
        filter_config = get_job_filter_config(settings.job_filter_config_path)

    score = 0.3
    reasons: list[str] = []

    title = job.title.lower()
    text = job.jd_text.lower()
    location = job.location.lower()

    if any(keyword in title for keyword in filter_config.score_title_keywords):
        score += 0.25
        reasons.append("title fit")

    if any(keyword in text for keyword in filter_config.score_new_grad_keywords):
        score += 0.25
        reasons.append("new grad phrase")

    if any(keyword in location for keyword in filter_config.score_geo_keywords):
        score += 0.1
        reasons.append("geo boost")

    if "remote" in location or "hybrid" in location:
        score += 0.05
        reasons.append("remote/hybrid")

    final_score = round(_clamp(score), 2)
    rationale = ", ".join(reasons) if reasons else "baseline interest"
    return ScoreResult(score=final_score, rationale=rationale)
