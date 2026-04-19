from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_jobs.core.enums import FeedbackRole
from agentic_jobs.db import models
from agentic_jobs.services.drafts.generator import DraftGenerator, DraftGeneratorError
from agentic_jobs.services.memory.store import MemoryStore
from agentic_jobs.services.slack.client import SlackClient, SlackError
from agentic_jobs.services.slack.tracker import MasterTracker


LOGGER = logging.getLogger(__name__)


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
    if not thread_ts:
        return

    # ------------------------------------------------------------------
    # !remember command — save as long-term memory, skip draft regen
    # ------------------------------------------------------------------
    if text.lower().startswith("!remember"):
        note = text[len("!remember"):].strip()
        if note:
            # Look up application for context (optional — memory is global)
            application = session.execute(
                select(models.Application).where(models.Application.slack_thread_ts == thread_ts)
            ).scalar_one_or_none()

            try:
                memory = MemoryStore(session)
                memory.save_explicit(
                    note,
                    application_id=application.id if application else None,
                )
                await slack_client.post_thread_message(
                    channel=event.get("channel", ""),
                    thread_ts=thread_ts,
                    text=f":brain: Remembered: _{note}_",
                )
                LOGGER.debug("Saved !remember note: %s", note[:80])
            except Exception:  # noqa: BLE001
                LOGGER.warning("Failed to save !remember note")
        return

    application = session.execute(
        select(models.Application).where(models.Application.slack_thread_ts == thread_ts)
    ).scalar_one_or_none()

    if not application:
        return

    feedback = models.ApplicationFeedback(
        application_id=application.id,
        role=FeedbackRole.USER,
        author=user_id,
        text=text,
    )
    session.add(feedback)
    try:
        session.commit()
        LOGGER.debug("Stored feedback note for %s", application.human_id)
    except Exception:  # noqa: BLE001
        session.rollback()
        LOGGER.exception("Failed to store feedback note for %s", application.human_id)
        return

    generator = DraftGenerator(session, slack_client)
    try:
        await generator.generate(
            application.id,
            notes=[text],
            author=user_id,
            post_to_slack=True,
            persist_notes=False,
        )
        tracker = MasterTracker(session, slack_client)
        try:
            await tracker.refresh()
        except SlackError:
            LOGGER.debug("Failed to refresh tracker after auto-regenerate for %s", application.human_id)
    except DraftGeneratorError as exc:
        LOGGER.warning("Failed to auto-regenerate for %s: %s", application.human_id, exc)
