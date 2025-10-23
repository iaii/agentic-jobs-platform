from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agentic_jobs.core.enums import ApplicationStatus, DomainReviewStatus
from agentic_jobs.db import models
from agentic_jobs.services.ranking import score_job
from agentic_jobs.services.slack.client import SlackClient


class SlackActionError(RuntimeError):
    """Raised for malformed or unsupported Slack actions."""


def _extract_user_name(payload: dict[str, Any]) -> str:
    user = payload.get("user") or {}
    return user.get("username") or user.get("name") or user.get("id") or "unknown"


def _parse_job_id(action_value: str) -> UUID:
    try:
        return UUID(action_value)
    except (ValueError, AttributeError) as exc:
        raise SlackActionError("Invalid job identifier") from exc


def _build_thread_blocks(job: models.Job, score: float, rationale: str, app_human_id: str) -> list[dict[str, Any]]:
    text = (
        f"*{job.title}* · {job.company_name} · {job.location}\n"
        f"<{job.url}|Open job description>\n"
        f"*Score:* `{score:.2f}` — {rationale}\n"
        f"*Canonical ID:* `{job.job_id_canonical}`\n"
        f"*Status:* `Queued`\n"
        f"*Application:* `{app_human_id}`"
    )
    return [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]


def _next_human_id(session: Session) -> str:
    now = datetime.now(tz=timezone.utc)
    prefix = f"APP-{now.year}-"
    stmt = (
        select(models.Application.human_id)
        .where(models.Application.human_id.like(f"{prefix}%"))
        .order_by(models.Application.human_id.desc())
        .limit(1)
    )
    last_id = session.execute(stmt).scalar_one_or_none()
    if last_id:
        try:
            next_seq = int(last_id.split("-")[-1]) + 1
        except ValueError:
            next_seq = 1
    else:
        next_seq = 1
    return f"{prefix}{next_seq:03d}"


async def handle_save_to_tracker(
    payload: dict[str, Any],
    session: Session,
    slack_client: SlackClient,
) -> dict[str, Any]:
    action = (payload.get("actions") or [])[0]
    job_id = _parse_job_id(action.get("value"))
    job = session.get(models.Job, job_id)
    if job is None:
        raise SlackActionError("Job not found for tracker save.")

    existing_app = session.execute(
        select(models.Application).where(models.Application.canonical_job_id == job.job_id_canonical)
    ).scalar_one_or_none()
    if existing_app:
        return {
            "text": f"Already tracked as {existing_app.human_id}.",
        }

    score_result = score_job(job)
    human_id = _next_human_id(session)
    app = models.Application(
        human_id=human_id,
        job_id=job.id,
        status=ApplicationStatus.QUEUED,
        score=score_result.score,
        canonical_job_id=job.job_id_canonical,
        submission_mode=job.submission_mode,
    )
    session.add(app)
    session.flush()

    # Prefer explicit message context; fall back to container context which Slack sends for blocks
    channel_id = payload.get("channel", {}).get("id")
    thread_ts = (payload.get("message") or {}).get("ts")
    if not channel_id:
        container = payload.get("container") or {}
        channel_id = container.get("channel_id") or container.get("channel")
    if not thread_ts:
        container = payload.get("container") or {}
        thread_ts = container.get("thread_ts") or container.get("message_ts") or container.get("ts")
    if not channel_id or not thread_ts:
        raise SlackActionError("Missing Slack channel or thread metadata.")

    response = await slack_client.post_thread_message(
        channel=channel_id,
        thread_ts=thread_ts,
        blocks=_build_thread_blocks(job, score_result.score, score_result.rationale, human_id),
    )
    app.slack_channel_id = channel_id
    app.slack_thread_ts = response.data.get("ts") or thread_ts

    session.commit()
    return {
        "text": f"Queued `{human_id}` with score {score_result.score:.2f}.",
    }


async def handle_needs_review_approve(
    payload: dict[str, Any],
    session: Session,
    slack_client: SlackClient,
) -> dict[str, Any]:
    action = (payload.get("actions") or [])[0]
    domain_root = action.get("value")
    if not domain_root:
        raise SlackActionError("Missing domain root for needs-review approval.")

    domain = session.execute(
        select(models.DomainReview).where(models.DomainReview.domain_root == domain_root)
    ).scalar_one_or_none()
    if domain is None:
        raise SlackActionError("Domain review record not found.")

    approver = _extract_user_name(payload)
    now_utc = datetime.now(tz=timezone.utc)

    whitelist_entry = models.Whitelist(
        domain_root=domain.domain_root,
        company_name=domain.company_name,
        ats_type=domain.ats_type,
        approved_by=approver,
        approved_at=now_utc,
    )
    try:
        session.merge(whitelist_entry)
    except IntegrityError:
        session.rollback()
        raise SlackActionError("Failed to insert whitelist entry.")

    domain.status = DomainReviewStatus.APPROVED
    domain.resolved_at = now_utc
    session.commit()

    channel_id = payload.get("channel", {}).get("id")
    message_ts = (payload.get("message") or {}).get("ts")
    if not channel_id:
        container = payload.get("container") or {}
        channel_id = container.get("channel_id") or container.get("channel")
    if not message_ts:
        container = payload.get("container") or {}
        message_ts = container.get("message_ts") or container.get("thread_ts") or container.get("ts")
    if channel_id and message_ts:
        text = f"`{domain.domain_root}` approved by {approver}."
        await slack_client.update_message(
            channel=channel_id,
            ts=message_ts,
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": text},
                }
            ],
        )

    return {"text": f"Approved `{domain_root}`."}


async def handle_needs_review_reject(
    payload: dict[str, Any],
    session: Session,
    slack_client: SlackClient,
    mute_days: int = 7,
) -> dict[str, Any]:
    action = (payload.get("actions") or [])[0]
    domain_root = action.get("value")
    if not domain_root:
        raise SlackActionError("Missing domain root for needs-review rejection.")

    domain = session.execute(
        select(models.DomainReview).where(models.DomainReview.domain_root == domain_root)
    ).scalar_one_or_none()
    if domain is None:
        raise SlackActionError("Domain review record not found.")

    reviewer = _extract_user_name(payload)
    now_utc = datetime.now(tz=timezone.utc)

    domain.status = DomainReviewStatus.MUTED
    domain.muted_until = now_utc + timedelta(days=mute_days)
    domain.resolved_at = None
    session.commit()

    channel_id = payload.get("channel", {}).get("id")
    message_ts = (payload.get("message") or {}).get("ts")
    if not channel_id:
        container = payload.get("container") or {}
        channel_id = container.get("channel_id") or container.get("channel")
    if not message_ts:
        container = payload.get("container") or {}
        message_ts = container.get("message_ts") or container.get("thread_ts") or container.get("ts")
    if channel_id and message_ts:
        text = (
            f"`{domain.domain_root}` muted by {reviewer}. "
            f"Next review after {domain.muted_until.date().isoformat()}."
        )
        await slack_client.update_message(
            channel=channel_id,
            ts=message_ts,
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": text},
                }
            ],
        )

    return {"text": f"Muted `{domain_root}` for {mute_days} days."}


async def handle_interactive_request(
    payload: dict[str, Any],
    session: Session,
    slack_client: SlackClient,
) -> dict[str, Any]:
    action_type = payload.get("type")
    if action_type != "block_actions":
        raise SlackActionError(f"Unsupported payload type: {action_type}")

    actions = payload.get("actions") or []
    if not actions:
        raise SlackActionError("No actions provided in payload.")

    action_id = actions[0].get("action_id")
    if action_id == "save_to_tracker":
        return await handle_save_to_tracker(payload, session, slack_client)
    if action_id == "needs_review_approve":
        return await handle_needs_review_approve(payload, session, slack_client)
    if action_id == "needs_review_reject":
        return await handle_needs_review_reject(payload, session, slack_client)

    raise SlackActionError(f"Unknown action: {action_id}")
