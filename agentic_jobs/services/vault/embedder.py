from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_jobs.config import settings
from agentic_jobs.db.models import VaultEmbedding
from agentic_jobs.services.vault.parser import VaultSection


LOGGER = logging.getLogger(__name__)

# Sections longer than this get truncated before embedding to stay within
# the embedding model's token window (~8192 tokens for nomic-embed-text).
_MAX_EMBED_CHARS = 6000


class VaultEmbedError(RuntimeError):
    """Raised when the embedding endpoint is unavailable or returns an error."""


class VaultEmbedder:
    """
    Generates and stores vector embeddings for vault sections using LM Studio's
    /v1/embeddings endpoint (OpenAI-compatible).

    Embeddings are stored as JSONB lists in the vault_embeddings table.
    Stale detection is done by comparing SHA-1 file hashes.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def embed_all(self, sections: list[VaultSection]) -> int:
        """Embed all sections and upsert to DB. Returns number of rows written."""
        count = 0
        for section in sections:
            vector = await self._get_embedding(section.text)
            self._upsert(section, vector)
            count += 1
        self.session.commit()
        # LOGGER.info("VaultEmbedder: embedded %d sections", count)
        return count

    async def refresh_stale(self, sections: list[VaultSection]) -> int:
        """
        Only re-embed sections whose file content has changed since last embed.
        Returns number of rows refreshed.
        """
        # Build a map of file_path → current hash from the incoming sections
        file_hashes: dict[str, str] = {}
        for section in sections:
            if section.file_path not in file_hashes:
                file_hashes[section.file_path] = self._hash_text(section.text)

        # Load stored hashes
        stored: dict[str, str] = {}
        rows = self.session.execute(
            select(VaultEmbedding.file_path, VaultEmbedding.file_hash).distinct()
        ).all()
        for file_path, file_hash in rows:
            stored[file_path] = file_hash

        stale_paths = {
            fp for fp, h in file_hashes.items() if stored.get(fp) != h
        }
        # Also include brand-new files not in stored at all
        stale_paths |= {fp for fp in file_hashes if fp not in stored}

        if not stale_paths:
            # LOGGER.info("VaultEmbedder: all embeddings up to date")
            return 0

        stale_sections = [s for s in sections if s.file_path in stale_paths]
        count = 0
        for section in stale_sections:
            try:
                vector = await self._get_embedding(section.text)
                self._upsert(section, vector)
                count += 1
            except VaultEmbedError as exc:
                LOGGER.warning("Failed to embed section '%s': %s", section.heading, exc)
        self.session.commit()
        # LOGGER.info("VaultEmbedder: refreshed %d sections across %d files", count, len(stale_paths))
        return count

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_embedding(self, text: str) -> list[float]:
        truncated = text[:_MAX_EMBED_CHARS]
        endpoint = settings.embedding_endpoint_url
        model = settings.embedding_model_name
        api_key = settings.llm_api_key
        if not api_key:
            raise RuntimeError(
                "LLM_API_KEY is not configured. For local backends (LM Studio, Ollama) set it to any non-empty string."
            )

        async with httpx.AsyncClient(timeout=settings.embedding_timeout_seconds) as client:
            try:
                response = await client.post(
                    endpoint,
                    json={"model": model, "input": truncated},
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise VaultEmbedError(
                    f"Embedding endpoint HTTP error {exc.response.status_code}"
                ) from exc
            except httpx.RequestError as exc:
                raise VaultEmbedError(f"Embedding endpoint unreachable: {exc}") from exc

        data = response.json()
        try:
            return data["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError) as exc:
            raise VaultEmbedError("Unexpected embedding response format") from exc

    def _upsert(self, section: VaultSection, vector: list[float]) -> None:
        """Insert or update a single VaultEmbedding row."""
        file_hash = self._hash_text(section.text)
        existing = self.session.execute(
            select(VaultEmbedding).where(
                VaultEmbedding.file_path == section.file_path,
                VaultEmbedding.heading == section.heading,
            )
        ).scalar_one_or_none()

        if existing:
            existing.section_text = section.text
            existing.wikilinks = section.wikilinks
            existing.embedding = vector
            existing.file_hash = file_hash
        else:
            row = VaultEmbedding(
                file_path=section.file_path,
                heading=section.heading,
                section_text=section.text,
                wikilinks=section.wikilinks,
                embedding=vector,
                file_hash=file_hash,
            )
            self.session.add(row)

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    @staticmethod
    async def health_check() -> bool:
        """Returns True if the embedding endpoint is reachable."""
        endpoint = settings.embedding_endpoint_url
        api_key = settings.llm_api_key
        if not api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
                resp = await client.post(
                    endpoint,
                    json={"model": settings.embedding_model_name, "input": "ping"},
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                return resp.status_code < 500
        except Exception:
            return False
