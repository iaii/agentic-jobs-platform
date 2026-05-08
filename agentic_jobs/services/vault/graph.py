from __future__ import annotations

from collections import deque

from agentic_jobs.services.vault.parser import VaultSection


class WikilinkGraph:
    """
    Builds a bidirectional adjacency graph from Obsidian [[wikilink]] relationships.

    Node keys are normalized heading names (lowercase, stripped of path prefix).
    When a section is retrieved by the embedder, this graph is used to expand
    the result set with directly-linked sections (1-hop by default).
    """

    def __init__(self, sections: list[VaultSection]) -> None:
        # Map normalized heading → VaultSection (last one wins on collision)
        self._by_heading: dict[str, VaultSection] = {}
        # Map normalized heading → set of linked headings
        self._adj: dict[str, set[str]] = {}
        self._build(sections)

    def _build(self, sections: list[VaultSection]) -> None:
        for section in sections:
            key = section.heading.lower()
            self._by_heading[key] = section
            self._adj.setdefault(key, set())
            for link in section.wikilinks:
                self._adj[key].add(link)
                # Reverse edge so that searching "Cyclic Sort" also surfaces
                # sections that link TO it.
                self._adj.setdefault(link, set()).add(key)

    def neighbors(self, heading: str, depth: int = 1) -> list[VaultSection]:
        """
        Return all VaultSection objects reachable within `depth` hops from `heading`.
        The source section itself is NOT included.
        """
        start = heading.lower()
        visited: set[str] = {start}
        queue: deque[tuple[str, int]] = deque()

        for neighbor in self._adj.get(start, set()):
            if neighbor not in visited:
                queue.append((neighbor, 1))

        results: list[VaultSection] = []
        while queue:
            node, current_depth = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            section = self._by_heading.get(node)
            if section:
                results.append(section)
            if current_depth < depth:
                for neighbor in self._adj.get(node, set()):
                    if neighbor not in visited:
                        queue.append((neighbor, current_depth + 1))

        return results

    def get_section(self, heading: str) -> VaultSection | None:
        return self._by_heading.get(heading.lower())

    def all_headings(self) -> list[str]:
        return list(self._by_heading.keys())
