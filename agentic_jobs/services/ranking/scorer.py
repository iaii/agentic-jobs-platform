from __future__ import annotations

from dataclasses import dataclass

from agentic_jobs.db import models


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


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(value, maximum))


def score_job(job: models.Job) -> ScoreResult:
    """Deterministic placeholder scorer until MVPart 4 lands."""
    score = 0.3
    reasons: list[str] = []

    title = job.title.lower()
    text = job.jd_text.lower()
    location = job.location.lower()

    if any(keyword in title for keyword in TITLE_KEYWORDS):
        score += 0.25
        reasons.append("title fit")

    if any(keyword in text for keyword in NEW_GRAD_KEYWORDS):
        score += 0.25
        reasons.append("new grad phrase")

    if any(keyword in location for keyword in GEO_KEYWORDS):
        score += 0.1
        reasons.append("geo boost")

    if "remote" in location or "hybrid" in location:
        score += 0.05
        reasons.append("remote/hybrid")

    final_score = round(_clamp(score), 2)
    rationale = ", ".join(reasons) if reasons else "baseline interest"
    return ScoreResult(score=final_score, rationale=rationale)
