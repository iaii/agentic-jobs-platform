from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from agentic_jobs.core.enums import TrustVerdict
from agentic_jobs.services.trust.whitelist import lookup_auto_whitelist


@dataclass(slots=True)
class TrustResult:
    score: int
    verdict: TrustVerdict
    signals: list[dict[str, str]] = field(default_factory=list)


async def evaluate(url: str, domain_root: str) -> TrustResult:
    host = (domain_root or urlparse(url).netloc).lower()
    entry = lookup_auto_whitelist(host)

    if entry is not None:
        signals = [
            {"signal": "host", "value": host},
            {"signal": "ats_type", "value": entry.ats_type},
            {"signal": "whitelist", "value": "match"},
        ]
        return TrustResult(score=90, verdict=TrustVerdict.AUTO_SAFE, signals=signals)

    signals = [
        {"signal": "host", "value": host},
        {"signal": "whitelist", "value": "none"},
    ]
    return TrustResult(score=30, verdict=TrustVerdict.NEEDS_HUMAN_APPROVAL, signals=signals)
