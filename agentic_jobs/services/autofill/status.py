from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_jobs.core.enums import AutofillTaskStatus
from agentic_jobs.db import models
from agentic_jobs.services.autofill.notifications import post_ops_update
from agentic_jobs.services.autofill.types import AutofillStatusUpdate
from agentic_jobs.services.slack.client import SlackClient, SlackError


LOGGER = logging.getLogger(__name__)


async def process_status_update(
    session: Session,
    application: models.Application,
    update: AutofillStatusUpdate,
    slack_client: SlackClient | None,
) -> models.AutofillTask:
    task = _latest_task(session, application.id)
    if task is None:
        raise RuntimeError("No autofill task found for application.")

    now = datetime.now(tz=timezone.utc)
    task.status = update.status
    if update.final_url:
        task.final_url = update.final_url
    metadata = dict(task.payload_metadata or {})
    if update.metadata:
        metadata.update(update.metadata)
    task.payload_metadata = metadata
    if update.status == AutofillTaskStatus.IN_PROGRESS and not task.started_at:
        task.started_at = now
    if update.status in {
        AutofillTaskStatus.READY,
        AutofillTaskStatus.BLOCKED,
        AutofillTaskStatus.FAILED,
        AutofillTaskStatus.SKIPPED,
    }:
        task.finished_at = now
    if update.status in {AutofillTaskStatus.BLOCKED, AutofillTaskStatus.FAILED}:
        task.last_error = update.blocked_reason or update.message
    session.add(task)
    session.commit()

    if slack_client:
        await _notify_slack(application, update, slack_client)
    return task


def _latest_task(session: Session, application_id) -> models.AutofillTask | None:
    stmt = (
        select(models.AutofillTask)
        .where(models.AutofillTask.application_id == application_id)
        .order_by(models.AutofillTask.created_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


async def _notify_slack(
    application: models.Application,
    update: AutofillStatusUpdate,
    slack_client: SlackClient,
) -> None:
    status_text = update.status.value.replace("_", " ").title()
    message = update.message or update.blocked_reason or ""
    text = f"Autofill `{application.human_id}` → *{status_text}*"
    if message:
        text += f" — {message}"
    if update.final_url:
        text += f"\nFinal URL: {update.final_url}"

    await post_ops_update(slack_client, text=text)

    if application.slack_channel_id and application.slack_thread_ts:
        try:
            await slack_client.post_thread_message(
                channel=application.slack_channel_id,
                thread_ts=application.slack_thread_ts,
                text=text,
            )
        except SlackError:
            LOGGER.warning("Failed to post autofill status to thread for %s", application.human_id)
