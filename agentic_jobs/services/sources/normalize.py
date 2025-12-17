from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from typing import Any


class _HTMLStripper(HTMLParser):
    BLOCK_TAGS = {
        "p",
        "div",
        "br",
        "li",
        "section",
        "article",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }
    IGNORE_TAGS = {"script", "style", "noscript"}

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._ignore_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.IGNORE_TAGS:
            self._ignore_depth += 1
            return
        if self._ignore_depth == 0 and tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.IGNORE_TAGS and self._ignore_depth:
            self._ignore_depth -= 1
            return
        if self._ignore_depth == 0 and tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignore_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        combined = "".join(self._parts)
        lines = [re.sub(r"\s+", " ", line).strip() for line in combined.splitlines()]
        filtered = [line for line in lines if line]
        return "\n".join(filtered)


def html_to_text(html: str) -> str:
    """Convert HTML into normalized plain text."""
    stripper = _HTMLStripper()
    stripper.feed(html)
    stripper.close()
    return stripper.get_text()


class _RequirementExtractor(HTMLParser):
    IGNORE_TAGS = {"script", "style", "noscript"}

    def __init__(self) -> None:
        super().__init__()
        self._ignore_depth = 0
        self._in_li = False
        self._buffer: list[str] = []
        self.items: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.IGNORE_TAGS:
            self._ignore_depth += 1
            return
        if self._ignore_depth == 0:
            if tag == "li":
                self._in_li = True
                self._buffer = []
            elif tag == "br" and self._in_li:
                self._buffer.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.IGNORE_TAGS and self._ignore_depth:
            self._ignore_depth -= 1
            return
        if self._ignore_depth == 0 and tag == "li" and self._in_li:
            text = "".join(self._buffer).strip()
            if text:
                normalized = re.sub(r"\s+", " ", text)
                self.items.append(normalized)
            self._in_li = False
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._ignore_depth == 0 and self._in_li:
            self._buffer.append(data)


def extract_requirements(html: str) -> list[dict[str, Any]]:
    """Extract bullet-list requirements from job description HTML."""
    parser = _RequirementExtractor()
    parser.feed(html)
    parser.close()

    bullets: list[dict[str, Any]] = [
        {"type": "bullet", "value": item}
        for item in parser.items
        if item
    ]

    if bullets:
        return bullets

    # Fallback: derive from text paragraphs with requirement keywords.
    text = html_to_text(html)
    for paragraph in text.split("\n"):
        lower = paragraph.lower()
        if any(keyword in lower for keyword in ("require", "must", "responsible")):
            bullets.append({"type": "text", "value": paragraph})

    return bullets


def compute_hash(*components: str) -> str:
    """Compute a stable SHA-1 hash for deduplication."""
    normalized_parts: list[str] = []
    for component in components:
        if component is None:
            continue
        normalized_parts.append(component.strip().lower())
    normalized = "|".join(normalized_parts)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()
