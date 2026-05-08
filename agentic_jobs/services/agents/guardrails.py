from __future__ import annotations

"""
Prompt injection detection for scraped web content and user notes.

Any text sourced from outside the system (scraped HTML, Slack messages) passes
through ``sanitize()`` before being embedded in an LLM prompt.  Suspicious
passages are stripped and a warning is logged so the pipeline continues with
clean input rather than failing hard.

Usage
-----
From the scraper (already called automatically via CompanyScraper._extract_text):

    from agentic_jobs.services.agents.guardrails import sanitize
    clean_text = sanitize(raw_text, source="scrape:example.com")

From the coordinator for user notes:

    from agentic_jobs.services.agents.guardrails import sanitize
    clean_notes = [sanitize(n, source="slack:user_note") for n in notes if n]

Prompt Improver integration point (future):
    Once the prompt improver is built, call it *after* sanitize() so the
    improver itself only ever sees clean input.  In coordinator.py, the
    clean_notes list is the right place:

        clean_notes = [sanitize(n, source="slack") for n in (notes or []) if n]
        # Future: clean_notes = await improve_user_prompt(clean_notes)
"""

import logging
import re

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Detection patterns
# ---------------------------------------------------------------------------
# Each pattern is compiled once.  All are case-insensitive.

_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(previous|prior|all)\s+instructions", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+a\b", re.IGNORECASE),
    re.compile(r"disregard\s+(your|the)\s+(instructions|rules|guidelines)", re.IGNORECASE),
    re.compile(r"\bsystem\s*prompt\b", re.IGNORECASE),
    re.compile(r"</?(?:system|user|assistant)>", re.IGNORECASE),
    re.compile(r"act\s+as\s+(if\s+you\s+(are|were)|a\b)", re.IGNORECASE),
    re.compile(r"forget\s+(everything|all)\s+(you|you've|you\s+have)\s+(been\s+)?told", re.IGNORECASE),
    re.compile(r"new\s+instruction[s]?\s*:", re.IGNORECASE),
    re.compile(r"do\s+not\s+(follow|obey|respect)\s+(the\s+)?(previous|prior|above)\s+(instruction|rule)", re.IGNORECASE),
]


def has_injection(text: str) -> bool:
    """Return True if ``text`` contains prompt injection patterns."""
    return any(p.search(text) for p in _PATTERNS)


def sanitize(text: str, *, source: str = "unknown") -> str:
    """
    Strip lines containing prompt injection patterns from ``text``.

    Each line is checked independently.  If a suspicious line is found it is
    replaced with a whitespace-only placeholder so surrounding context isn't
    disrupted, and a warning is logged.

    Returns the cleaned text.  If no injection is detected the original string
    is returned unchanged (no copy).
    """
    if not has_injection(text):
        return text

    clean_lines: list[str] = []
    stripped_count = 0
    for line in text.splitlines():
        if any(p.search(line) for p in _PATTERNS):
            stripped_count += 1
            clean_lines.append("")  # preserve line structure
        else:
            clean_lines.append(line)

    LOGGER.warning(
        "Prompt injection detected in %s — stripped %d line(s)",
        source,
        stripped_count,
    )
    return "\n".join(clean_lines)
