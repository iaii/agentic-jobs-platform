from __future__ import annotations

from dataclasses import dataclass, field

from agentic_jobs.core.enums import TrustVerdict


@dataclass(slots=True)
class TrustResult:
    score: int
    verdict: TrustVerdict
    signals: list[dict[str, str]] = field(default_factory=list)


async def evaluate(url: str, domain_root: str) -> TrustResult:
    """Basic trust evaluator stub for Greenhouse-hosted jobs."""
    # Greenhouse is treated as a trusted ATS host by default.
    signals = [
        {"signal": "host", "value": domain_root},
        {"signal": "provenance", "value": "greenhouse"},
    ]
    return TrustResult(score=85, verdict=TrustVerdict.AUTO_SAFE, signals=signals)
