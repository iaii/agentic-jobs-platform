from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_jobs.config import settings
from agentic_jobs.db.models import VaultEmbedding
from agentic_jobs.services.vault.embedder import VaultEmbedder, VaultEmbedError
from agentic_jobs.services.vault.graph import WikilinkGraph
from agentic_jobs.services.vault.parser import VaultSection


LOGGER = logging.getLogger(__name__)

# Max characters of section text to include in a VaultMatch result sent to agents.
# Keeps LLM context usage under control.
_MAX_SECTION_CHARS = 1200
_MAX_LINKED_CHARS = 600


@dataclass(slots=True)
class VaultMatch:
    heading: str
    file_path: str
    text: str                              # truncated to _MAX_SECTION_CHARS
    score: float                           # cosine similarity 0-1
    linked_sections: list[str] = field(default_factory=list)  # text snippets from wikilink neighbors


class VaultRetriever:
    """
    Semantic search over vault embeddings with wikilink graph expansion.

    Flow:
      1. Embed the query string via LM Studio /v1/embeddings
      2. Load all stored embeddings from DB and compute cosine similarity in numpy
      3. Return top_k matches
      4. For each match, expand via WikilinkGraph.neighbors() to pull in related sections
    """

    def __init__(self, session: Session, graph: WikilinkGraph) -> None:
        self.session = session
        self.graph = graph
        self._embedder = VaultEmbedder(session)

    async def search(
        self,
        query: str,
        top_k: int | None = None,
        link_depth: int | None = None,
    ) -> list[VaultMatch]:
        """
        Return top_k semantically similar vault sections with wikilink-expanded context.
        Falls back to an empty list if embeddings are unavailable.
        """
        k = top_k if top_k is not None else settings.vault_top_k
        depth = link_depth if link_depth is not None else settings.vault_link_depth

        # Load all stored embeddings
        rows = self.session.execute(
            select(VaultEmbedding).where(VaultEmbedding.embedding.is_not(None))
        ).scalars().all()

        if not rows:
            LOGGER.warning("VaultRetriever: no embeddings found in DB — run VaultEmbedder first")
            return []

        # Embed the query
        try:
            query_vec = await self._embedder._get_embedding(query)
        except VaultEmbedError as exc:
            LOGGER.warning("VaultRetriever: embedding query failed (%s) — skipping vault search", exc)
            return []

        # Compute cosine similarities in numpy
        q = np.array(query_vec, dtype=np.float32)
        q_norm = np.linalg.norm(q)
        if q_norm == 0:
            return []

        scored: list[tuple[float, VaultEmbedding]] = []
        for row in rows:
            vec = np.array(row.embedding, dtype=np.float32)
            norm = np.linalg.norm(vec)
            if norm == 0:
                continue
            score = float(np.dot(q, vec) / (q_norm * norm))
            scored.append((score, row))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:k]

        results: list[VaultMatch] = []
        for score, row in top:
            linked_texts = self._expand_links(row.heading, depth)
            results.append(
                VaultMatch(
                    heading=row.heading,
                    file_path=row.file_path,
                    text=row.section_text[:_MAX_SECTION_CHARS],
                    score=round(score, 4),
                    linked_sections=linked_texts,
                )
            )

        return results

    def _expand_links(self, heading: str, depth: int) -> list[str]:
        """Retrieve text snippets from wikilink-connected sections."""
        neighbors = self.graph.neighbors(heading, depth=depth)
        snippets: list[str] = []
        for neighbor in neighbors:
            text = neighbor.text[:_MAX_LINKED_CHARS]
            snippets.append(f"[{neighbor.heading}] {text}")
        return snippets

    # ------------------------------------------------------------------
    # Convenience factory
    # ------------------------------------------------------------------

    @classmethod
    def from_sections(cls, session: Session, sections: list[VaultSection]) -> "VaultRetriever":
        """Build a retriever from an in-memory list of sections (graph is built immediately)."""
        graph = WikilinkGraph(sections)
        return cls(session, graph)
