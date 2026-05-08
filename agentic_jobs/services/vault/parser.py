from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


# Matches any markdown heading: # Heading, ## Heading, etc.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
# Matches [[Link]] or [[path/Link|Display]]
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


@dataclass(slots=True)
class VaultSection:
    file_path: str          # relative path from vault root, e.g. "DSA/Arrays.md"
    heading: str            # section heading text, e.g. "Cyclic Sort"
    heading_level: int      # 1-6
    text: str               # full section text (heading line + body)
    wikilinks: list[str] = field(default_factory=list)  # extracted [[link]] targets


class VaultParser:
    """Parse all .md files in an Obsidian vault folder into VaultSection objects."""

    def __init__(self, vault_path: Path) -> None:
        self.vault_path = vault_path

    def parse_all(self) -> list[VaultSection]:
        sections: list[VaultSection] = []
        for md_file in sorted(self.vault_path.rglob("*.md")):
            # Skip Obsidian internals
            if ".obsidian" in md_file.parts:
                continue
            rel_path = str(md_file.relative_to(self.vault_path))
            sections.extend(self._parse_file(md_file, rel_path))
        return sections

    def _parse_file(self, path: Path, rel_path: str) -> list[VaultSection]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return []

        if not text.strip():
            return []

        # Find all heading positions
        matches = list(_HEADING_RE.finditer(text))

        if not matches:
            # File has no headings — treat entire content as one unnamed section
            wikilinks = self._extract_wikilinks(text)
            if text.strip():
                return [
                    VaultSection(
                        file_path=rel_path,
                        heading=path.stem,
                        heading_level=1,
                        text=text.strip(),
                        wikilinks=wikilinks,
                    )
                ]
            return []

        sections: list[VaultSection] = []

        # Any text before the first heading becomes a preamble section
        preamble = text[: matches[0].start()].strip()
        if preamble:
            wikilinks = self._extract_wikilinks(preamble)
            sections.append(
                VaultSection(
                    file_path=rel_path,
                    heading=path.stem,
                    heading_level=1,
                    text=preamble,
                    wikilinks=wikilinks,
                )
            )

        for i, match in enumerate(matches):
            level = len(match.group(1))
            # Strip [[wikilinks]] from heading text (e.g. "### [[Cyclic Sort]]" → "Cyclic Sort")
            raw_heading = match.group(2).strip()
            wl_match = _WIKILINK_RE.search(raw_heading)
            heading_text = wl_match.group(1).split("/")[-1].strip() if wl_match else raw_heading
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            section_text = text[start:end].strip()
            wikilinks = self._extract_wikilinks(section_text)
            sections.append(
                VaultSection(
                    file_path=rel_path,
                    heading=heading_text,
                    heading_level=level,
                    text=section_text,
                    wikilinks=wikilinks,
                )
            )

        return sections

    @staticmethod
    def _extract_wikilinks(text: str) -> list[str]:
        """Extract all [[Link]] targets from text, normalized to lowercase."""
        links = []
        for match in _WIKILINK_RE.finditer(text):
            raw = match.group(1).strip()
            # Strip path prefix if present (e.g. "Computer Science/DSA/Arrays" → "Arrays")
            name = raw.split("/")[-1].strip()
            if name:
                links.append(name.lower())
        return list(dict.fromkeys(links))  # deduplicate, preserve order
