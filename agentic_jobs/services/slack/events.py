from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_jobs.core.enums import ArtifactType, FeedbackRole
from agentic_jobs.db import models
from agentic_jobs.services.agents.coordinator import PipelineCoordinator, PipelineCoordinatorError
from agentic_jobs.services.artifacts.utils import (
    ARTIFACTS_DIR,
    load_artifact_text,
)
from agentic_jobs.services.memory.store import MemoryStore
from agentic_jobs.services.slack.client import SlackClient, SlackError
from agentic_jobs.services.slack.tracker import MasterTracker


LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------

class ThreadIntent(str, Enum):
    STRUCTURAL = "structural"        # direct text edit — no LLM needed
    CONTENT_REVISION = "revision"    # WriterAgent only, keep existing research
    FULL_PIPELINE = "full_pipeline"  # full researcher + writer + reviewer rerun


# Patterns that indicate a structural change the code can apply directly.
# Each entry: (compiled regex, handler tag)
_NAME_CHANGE_RE = re.compile(
    r"(?:change|update|use|sign|make)\s+(?:my\s+)?name\s+(?:to|as)\s+([A-Za-z][A-Za-z\s\.\-]+?)(?:\s*$|\s+(?:in|on|for|at|please|thanks))",
    re.IGNORECASE,
)
_SIGN_AS_RE = re.compile(
    r"sign\s+(?:it\s+)?(?:the\s+letter\s+)?as\s+([A-Za-z][A-Za-z\s\.\-]+?)(?:\s*$|\s+(?:please|thanks))",
    re.IGNORECASE,
)
_HEADING_RE = re.compile(
    r"add\s+(?:a\s+)?(?:heading|header|title)[^\#]*(#[^\n]+)|^(#+\s+\S[^\n]*)",
    re.IGNORECASE | re.MULTILINE,
)
_ADD_COVER_LETTER_HEADING_RE = re.compile(
    r"add\s+(?:a\s+)?(?:#\s*cover\s+letter|cover\s+letter\s+heading)",
    re.IGNORECASE,
)

# Explicit signals that the user wants a full re-research
_FULL_PIPELINE_RE = re.compile(
    r"\b(?:regenerate|start\s+over|redo|from\s+scratch|full\s+rewrite|new\s+draft)\b",
    re.IGNORECASE,
)


def _classify(text: str) -> ThreadIntent:
    """Classify a thread message into a routing intent."""
    if _FULL_PIPELINE_RE.search(text):
        return ThreadIntent.FULL_PIPELINE

    # Structural: name change
    if _NAME_CHANGE_RE.search(text) or _SIGN_AS_RE.search(text):
        return ThreadIntent.STRUCTURAL

    # Structural: heading add
    if _HEADING_RE.search(text) or _ADD_COVER_LETTER_HEADING_RE.search(text):
        return ThreadIntent.STRUCTURAL

    # Everything else: content revision (writer only, no re-research)
    return ThreadIntent.CONTENT_REVISION


# ---------------------------------------------------------------------------
# Structural edit handler — applies changes directly to the artifact file
# ---------------------------------------------------------------------------

_SIGNOFF_WORDS = r"(?:Best regards|Sincerely|Warm regards|Kind regards|Regards)"


def _apply_name_change(letter: str, new_name: str) -> str:
    """Replace the signoff name in the cover letter."""
    new_name = new_name.strip()
    # Primary: signoff on its own line, name on next line(s) — handles \n and \n\n
    updated = re.sub(
        rf"({_SIGNOFF_WORDS},?\s*\n+\s*)(\S[^\n]*)",
        lambda m: m.group(1) + new_name,
        letter,
        flags=re.IGNORECASE,
    )
    if updated != letter:
        return updated
    # Fallback: name on same line as signoff ("Best regards, Apoorva Chilukuri")
    updated = re.sub(
        rf"({_SIGNOFF_WORDS},\s+)(\S[^\n]*)",
        lambda m: m.group(1) + new_name,
        letter,
        flags=re.IGNORECASE,
    )
    return updated


def _apply_heading(letter: str, heading: str) -> str:
    """Prepend a markdown heading to the cover letter if not already present."""
    heading = heading.strip()
    if letter.lstrip().startswith("#"):
        # Already has a heading — replace it
        return re.sub(r"^(#+\s+[^\n]+\n*)", heading + "\n\n", letter.lstrip(), count=1)
    return heading + "\n\n" + letter


async def _handle_structural_edit(
    text: str,
    application: models.Application,
    session: Session,
    slack_client: SlackClient,
    channel: str,
    thread_ts: str,
) -> None:
    """Apply a structural edit directly to the artifact — no LLM involved."""
    letter = load_artifact_text(session, application.id, ArtifactType.COVER_LETTER_VERSION)
    if not letter:
        await slack_client.post_thread_message(
            channel=channel, thread_ts=thread_ts,
            text="_No cover letter found to edit. Generate one first._",
        )
        return

    edited = letter

    # Name change
    m = _NAME_CHANGE_RE.search(text) or _SIGN_AS_RE.search(text)
    if m:
        new_name = m.group(1).strip()
        edited = _apply_name_change(edited, new_name)
        LOGGER.info("[events] Applied name change to '%s' for %s", new_name, application.human_id)

    # Heading
    h = _HEADING_RE.search(text)
    heading_text = None
    if _ADD_COVER_LETTER_HEADING_RE.search(text):
        heading_text = "# Cover Letter"
    elif h:
        heading_text = (h.group(1) or h.group(2) or "").strip()

    if heading_text:
        edited = _apply_heading(edited, heading_text)
        LOGGER.info("[events] Applied heading '%s' for %s", heading_text, application.human_id)

    if edited == letter:
        await slack_client.post_thread_message(
            channel=channel, thread_ts=thread_ts,
            text="_Nothing to change — couldn't find the relevant part of the letter._",
        )
        return

    # Persist as a new artifact version
    from agentic_jobs.services.agents.coordinator import PipelineCoordinator
    from sqlalchemy import func
    from agentic_jobs.core.enums import ArtifactType as AT

    version_count = session.execute(
        select(func.count())
        .select_from(models.Artifact)
        .where(
            models.Artifact.application_id == application.id,
            models.Artifact.type == AT.COVER_LETTER_VERSION,
        )
    ).scalar_one() or 0
    version_number = version_count + 1

    artifact_dir = ARTIFACTS_DIR / application.human_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / f"cl-v{version_number}.md"
    path.write_text(edited, encoding="utf-8")

    session.add(models.Artifact(
        application_id=application.id,
        type=AT.COVER_LETTER_VERSION,
        uri=f"file://{path.resolve()}",
    ))
    session.add(models.ApplicationFeedback(
        application_id=application.id,
        role=FeedbackRole.ASSISTANT,
        text=edited,
    ))
    session.commit()

    await slack_client.post_thread_message(
        channel=channel, thread_ts=thread_ts,
        text=f"*Updated* (v{version_number})\n\n{edited}",
    )


# ---------------------------------------------------------------------------
# Main event handler
# ---------------------------------------------------------------------------

async def handle_slack_event(
    payload: dict[str, Any],
    session: Session,
    slack_client: SlackClient,
) -> None:
    event = payload.get("event") or {}
    if event.get("type") != "message":
        return
    if event.get("bot_id"):
        return
    text = (event.get("text") or "").strip()
    user_id = event.get("user")
    if not text or not user_id:
        return
    thread_ts = event.get("thread_ts") or event.get("ts")
    channel = event.get("channel", "")
    if not thread_ts:
        return

    # ------------------------------------------------------------------
    # !remember command
    # ------------------------------------------------------------------
    if text.lower().startswith("!help"):
        await slack_client.post_thread_message(
            channel=channel, thread_ts=thread_ts,
            text=(
                "*Available commands:*\n"
                "• `!remember <note>` — save a style or preference note to long-term memory\n"
                "• `!help` — show this message\n\n"
                "To revise the cover letter, just type your feedback directly (no `!` needed)."
            ),
        )
        return

    if text.lower().startswith("!remember"):
        note = text[len("!remember"):].strip()
        _MAX_REMEMBER_CHARS = 500
        if not note:
            await slack_client.post_thread_message(
                channel=channel, thread_ts=thread_ts,
                text="_Nothing to remember. Usage: `!remember <note>`_",
            )
            return
        if len(note) > _MAX_REMEMBER_CHARS:
            await slack_client.post_thread_message(
                channel=channel, thread_ts=thread_ts,
                text=f"_Note too long (max {_MAX_REMEMBER_CHARS} chars). Please shorten it._",
            )
            return
        application = session.execute(
            select(models.Application).where(models.Application.slack_thread_ts == thread_ts)
        ).scalar_one_or_none()
        try:
            memory = MemoryStore(session)
            memory.save_explicit(note, application_id=application.id if application else None)
            await slack_client.post_thread_message(
                channel=channel, thread_ts=thread_ts,
                text=f":brain: Remembered: _{note}_",
            )
        except Exception:  # noqa: BLE001
            LOGGER.warning("Failed to save !remember note")
            await slack_client.post_thread_message(
                channel=channel, thread_ts=thread_ts,
                text="_Failed to save note. Please try again._",
            )
        return

    if text.startswith("!"):
        await slack_client.post_thread_message(
            channel=channel, thread_ts=thread_ts,
            text=f"Unknown command `{text.split()[0]}`. Type `!help` to see available commands.",
        )
        return

    application = session.execute(
        select(models.Application).where(models.Application.slack_thread_ts == thread_ts)
    ).scalar_one_or_none()

    if not application:
        return

    # Store the user note regardless of routing path
    session.add(models.ApplicationFeedback(
        application_id=application.id,
        role=FeedbackRole.USER,
        author=user_id,
        text=text,
    ))
    try:
        session.commit()
    except Exception:  # noqa: BLE001
        session.rollback()
        LOGGER.exception("Failed to store feedback note for %s", application.human_id)
        return

    intent = _classify(text)
    LOGGER.info("[events] Thread message for %s classified as: %s", application.human_id, intent)

    coordinator = PipelineCoordinator(session, slack_client)

    if intent == ThreadIntent.STRUCTURAL:
        # Direct artifact edit — no LLM
        try:
            await _handle_structural_edit(text, application, session, slack_client, channel, thread_ts)
        except Exception:  # noqa: BLE001
            LOGGER.exception("Structural edit failed for %s", application.human_id)

    elif intent == ThreadIntent.CONTENT_REVISION:
        # WriterAgent only — preserve existing research, revise the existing draft
        try:
            await coordinator.run_revision(
                application.id,
                notes=[text],
                author=user_id,
                post_to_slack=True,
            )
        except PipelineCoordinatorError as exc:
            LOGGER.warning("Content revision failed for %s: %s", application.human_id, exc)

    else:
        # Full pipeline — researcher + writer + review loop
        try:
            await coordinator.run(
                application.id,
                notes=[text],
                author=user_id,
                post_to_slack=True,
            )
        except PipelineCoordinatorError as exc:
            LOGGER.warning("Full pipeline failed for %s: %s", application.human_id, exc)

    tracker = MasterTracker(session, slack_client)
    try:
        await tracker.refresh()
    except SlackError:
        LOGGER.debug("Failed to refresh tracker after thread event for %s", application.human_id)
