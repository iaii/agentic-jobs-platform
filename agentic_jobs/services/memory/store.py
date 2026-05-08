from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_jobs.config import settings
from agentic_jobs.core.enums import FeedbackRole, MemoryCategory, MemoryType
from agentic_jobs.db import models
from agentic_jobs.services.llm.runner import LlmBackendError, call_llm


LOGGER = logging.getLogger(__name__)

# Feedback notes shorter than this are treated as noise (reactions, acks)
_MIN_NOTE_LENGTH = 15

# Common low-signal phrases to skip during auto-assess condensing
_NOISE_PHRASES: frozenset[str] = frozenset([
    "ok", "okay", "looks good", "sounds good", "perfect", "good",
    "thanks", "thank you", "nice", "great", "cool", "got it",
    "yes", "no", "sure", "done", "lgtm",
])

# Max characters per feedback note sent to the LLM during auto-assess
_MAX_NOTE_CHARS = 200

# Source labels used to identify memory origins
_SOURCE_EXPLICIT = "user_explicit"    # !remember command
_SOURCE_AUTO = "auto_assessed"        # 3-day cron job
_SOURCE_PIPELINE = "pipeline"         # extracted from agent run


class MemoryStore:
    """
    Persistent memory for the cover letter pipeline.

    Two tiers:
      - Short-term: scoped to a specific application_id. Used to carry context
        across multiple generation rounds for the same application.
      - Long-term: application_id is NULL. Cross-application learnings that
        every future pipeline run will benefit from.

    Long-term memory is populated three ways:
      1. Explicitly via the !remember Slack command (source=user_explicit)
      2. Auto-assessed every 3 days from accumulated feedback (source=auto_assessed)
      3. Extracted from pipeline runs after finalize (source=pipeline)
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Explicit save (!remember command)
    # ------------------------------------------------------------------

    def save_explicit(self, content: str, application_id: UUID | None = None) -> None:
        """
        Save a user-provided note as long-term memory.
        Called when the user types !remember <note> in a Slack thread.
        """
        content = content.strip()
        if not content:
            return
        self.session.add(models.AgentMemory(
            application_id=application_id,
            memory_type=MemoryType.LONG_TERM,
            category=MemoryCategory.STYLE_PREFERENCE,
            content=content,
            source=_SOURCE_EXPLICIT,
        ))
        self.session.commit()
        LOGGER.info("Memory: saved explicit note (%d chars)", len(content))

    # ------------------------------------------------------------------
    # Short-term memory (per-application context)
    # ------------------------------------------------------------------

    def save_short_term(
        self,
        application_id: UUID,
        content: str,
        source_agent: str,
        category: MemoryCategory = MemoryCategory.FEEDBACK_PATTERN,
    ) -> None:
        expires = datetime.now(timezone.utc) + timedelta(days=7)
        self.session.add(models.AgentMemory(
            application_id=application_id,
            memory_type=MemoryType.SHORT_TERM,
            category=category,
            content=content,
            source=source_agent,
            expires_at=expires,
        ))

    def get_short_term(self, application_id: UUID) -> list[str]:
        now = datetime.now(timezone.utc)
        rows = self.session.execute(
            select(models.AgentMemory.content)
            .where(
                models.AgentMemory.application_id == application_id,
                models.AgentMemory.memory_type == MemoryType.SHORT_TERM,
                (models.AgentMemory.expires_at.is_(None))
                | (models.AgentMemory.expires_at > now),
            )
            .order_by(models.AgentMemory.created_at.desc())
        ).scalars().all()
        return list(rows)

    # ------------------------------------------------------------------
    # Long-term memory (cross-application learnings)
    # ------------------------------------------------------------------

    def save_long_term(
        self,
        content: str,
        category: MemoryCategory = MemoryCategory.STYLE_PREFERENCE,
        source: str = _SOURCE_PIPELINE,
    ) -> None:
        self.session.add(models.AgentMemory(
            application_id=None,
            memory_type=MemoryType.LONG_TERM,
            category=category,
            content=content,
            source=source,
        ))

    def get_long_term(
        self,
        limit: int = 5,
        category: MemoryCategory | None = None,
    ) -> list[str]:
        stmt = (
            select(models.AgentMemory.content)
            .where(models.AgentMemory.memory_type == MemoryType.LONG_TERM)
            .order_by(models.AgentMemory.created_at.desc())
        )
        if category is not None:
            stmt = stmt.where(models.AgentMemory.category == category)
        stmt = stmt.limit(limit)
        return list(self.session.execute(stmt).scalars().all())

    # ------------------------------------------------------------------
    # Auto-assess: 3-day condensing pipeline
    # ------------------------------------------------------------------

    async def auto_assess(self) -> int:
        """
        Condense accumulated user feedback into reusable long-term learnings.
        Returns the number of learnings extracted and saved.

        Pipeline:
          1. Find last auto-assess timestamp from most recent auto_assessed memory
          2. Query ApplicationFeedback(role=USER) notes since that timestamp
          3. Filter noise: too short, common ack phrases
          4. Truncate each to _MAX_NOTE_CHARS
          5. Batch to LLM: extract reusable learnings
          6. Save each learning as AgentMemory(type=LONG_TERM, source=auto_assessed)
        """
        last_assessed = self._last_assessment_time()
        raw_notes = self._fetch_feedback_since(last_assessed)

        if not raw_notes:
            LOGGER.info("Memory auto-assess: no new feedback since %s", last_assessed)
            return 0

        condensed = self._condense_notes(raw_notes)
        if len(condensed) < 3:
            LOGGER.info("Memory auto-assess: too few notes to assess (%d)", len(condensed))
            return 0

        LOGGER.info("Memory auto-assess: processing %d notes (from %d raw)", len(condensed), len(raw_notes))

        try:
            learnings = await self._extract_learnings_via_llm(condensed)
        except LlmBackendError as exc:
            LOGGER.warning("Memory auto-assess: LLM extraction failed: %s", exc)
            return 0

        for learning in learnings:
            self.save_long_term(
                content=learning,
                category=self._classify(learning),
                source=_SOURCE_AUTO,
            )

        self.session.commit()
        LOGGER.info("Memory auto-assess: saved %d learnings", len(learnings))
        return len(learnings)

    def _last_assessment_time(self) -> datetime:
        """Find timestamp of the most recent auto-assess run, or 3 days ago if none."""
        row = self.session.execute(
            select(models.AgentMemory.created_at)
            .where(models.AgentMemory.source == _SOURCE_AUTO)
            .order_by(models.AgentMemory.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row:
            return row.replace(tzinfo=timezone.utc) if row.tzinfo is None else row
        return datetime.now(timezone.utc) - timedelta(days=settings.memory_assessment_interval_days)

    def _fetch_feedback_since(self, since: datetime) -> list[str]:
        rows = self.session.execute(
            select(models.ApplicationFeedback.text)
            .where(
                models.ApplicationFeedback.role == FeedbackRole.USER,
                models.ApplicationFeedback.created_at > since,
            )
            .order_by(models.ApplicationFeedback.created_at)
        ).scalars().all()
        return list(rows)

    @staticmethod
    def _condense_notes(notes: list[str]) -> list[str]:
        """Filter noise and truncate — reduces LLM input volume before sending."""
        condensed: list[str] = []
        seen: set[str] = set()
        for note in notes:
            cleaned = note.strip()
            # Skip too-short notes
            if len(cleaned) < _MIN_NOTE_LENGTH:
                continue
            # Skip common ack phrases
            if cleaned.lower() in _NOISE_PHRASES:
                continue
            # Truncate
            truncated = cleaned[:_MAX_NOTE_CHARS]
            # Deduplicate by normalized form
            normalized = re.sub(r"\s+", " ", truncated.lower())
            if normalized in seen:
                continue
            seen.add(normalized)
            condensed.append(truncated)
        return condensed

    @staticmethod
    async def _extract_learnings_via_llm(notes: list[str]) -> list[str]:
        """Ask the LLM to extract reusable learnings from a condensed list of feedback notes."""
        bullet_list = "\n".join(f"- {note}" for note in notes)
        system = (
            "You are a writing coach analyzing feedback on cover letters. "
            "Your task: identify which notes contain reusable, generalizable preferences "
            "about tone, style, content, or structure that should apply to all future cover letters.\n\n"
            "Rules:\n"
            "- Extract only learnings that would be useful beyond this specific application\n"
            "- Ignore application-specific details (company names, job titles)\n"
            "- Write each learning as a clear, actionable instruction\n"
            "- If no generalizable preferences exist, return an empty list\n\n"
            'Respond ONLY with JSON: {"learnings": ["learning 1", "learning 2"]}'
        )
        user = f"Feedback notes to analyze:\n{bullet_list}"

        response = await call_llm(system, user, temperature=0.1)
        raw_learnings = response.content.get("learnings", [])
        return [str(l).strip() for l in raw_learnings if l and str(l).strip()]

    @staticmethod
    def _classify(content: str) -> MemoryCategory:
        """Simple heuristic to categorize a learning."""
        lower = content.lower()
        if any(w in lower for w in ["company", "research", "mission", "product"]):
            return MemoryCategory.COMPANY_INSIGHT
        if any(w in lower for w in ["tone", "voice", "style", "word", "phrase", "sentence", "write"]):
            return MemoryCategory.STYLE_PREFERENCE
        return MemoryCategory.FEEDBACK_PATTERN
