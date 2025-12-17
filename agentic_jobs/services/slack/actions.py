from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID
from urllib.parse import unquote, urlparse

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agentic_jobs.config import settings
from agentic_jobs.core.enums import (
    ApplicationStage,
    ApplicationStatus,
    ArtifactType,
    DomainReviewStatus,
)
from agentic_jobs.db import models
from agentic_jobs.db.session import SessionLocal
from agentic_jobs.services.drafts.generator import DraftGenerator, DraftGeneratorError
from agentic_jobs.services.ranking import score_job
from agentic_jobs.services.applications.stage import ARCHIVED_STAGES, apply_stage, stage_display
from agentic_jobs.services.slack.client import SlackClient, SlackError
from agentic_jobs.services.slack.tracker import MasterTracker
from agentic_jobs.services.llm.runner import LlmBackendError


LOGGER = logging.getLogger(__name__)
ARTIFACTS_DIR = Path("artifacts")
TRACKER_STAGE_OPTIONS: list[ApplicationStage] = [
    ApplicationStage.INTERESTED,
    ApplicationStage.COVER_LETTER_IN_PROGRESS,
    ApplicationStage.COVER_LETTER_FINALIZED,
    ApplicationStage.SUBMITTED,
    ApplicationStage.INTERVIEWING,
    ApplicationStage.ACCEPTED,
    ApplicationStage.REJECTED,
]


class SlackActionError(RuntimeError):
    """Raised for malformed or unsupported Slack actions."""


def _extract_user_name(payload: dict[str, Any]) -> str:
    user = payload.get("user") or {}
    return user.get("username") or user.get("name") or user.get("id") or "unknown"


def _extract_user_id(payload: dict[str, Any]) -> str | None:
    user = payload.get("user") or {}
    return user.get("id")


def _parse_action_job_context(value: str | None) -> tuple[UUID | None, str | None]:
    if not value:
        raise SlackActionError("Missing job identifier")
    job_uuid: UUID | None = None
    canonical_id: str | None = None
    try:
        data = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        data = None
    if isinstance(data, dict):
        raw_uuid = data.get("job_id")
        canonical_id = data.get("canonical_id")
        if raw_uuid:
            try:
                job_uuid = UUID(raw_uuid)
            except (ValueError, TypeError):
                job_uuid = None
        if canonical_id:
            canonical_id = str(canonical_id)
        else:
            canonical_id = None
    else:
        # Legacy payload: UUID string
        try:
            job_uuid = UUID(value)
        except (ValueError, TypeError):
            canonical_id = value
    if not job_uuid and not canonical_id:
        raise SlackActionError("Invalid job identifier")
    return job_uuid, canonical_id


def _parse_application_action_value(value: str | None) -> UUID:
    if not value:
        raise SlackActionError("Missing application identifier.")
    try:
        data = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise SlackActionError("Invalid application identifier payload.") from exc
    app_id = data.get("application_id")
    if not app_id:
        raise SlackActionError("Application identifier missing.")
    try:
        return UUID(app_id)
    except (TypeError, ValueError) as exc:
        raise SlackActionError("Malformed application identifier.") from exc


def _encode_action_value(payload: dict[str, Any]) -> str:
    return json.dumps(payload)


def _build_control_block(application_id: UUID) -> dict[str, Any]:
    payload = _encode_action_value({"application_id": str(application_id)})
    return {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Generate draft"},
                "action_id": "drafts_generate",
                "value": payload,
                "style": "primary",
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Finalize draft"},
                "action_id": "drafts_finalize",
                "value": payload,
                "style": "danger",
            },
        ],
    }


def _build_thread_blocks(
    job: models.Job,
    score: float,
    rationale: str,
    app_human_id: str,
    application_id: UUID,
) -> list[dict[str, Any]]:
    text = (
        f"*{job.title}* · {job.company_name} · {job.location}\n"
        f"<{job.url}|Open job description>\n"
        f"*Score:* `{score:.2f}` — {rationale}\n"
        f"*Canonical ID:* `{job.job_id_canonical}`\n"
        f"*Status:* `Queued`\n"
        f"*Application:* `{app_human_id}`"
    )
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        _build_control_block(application_id),
    ]


async def _refresh_tracker(session: Session, slack_client: SlackClient) -> None:
    if not settings.slack_jobs_tracker_channel:
        return
    tracker = MasterTracker(session, slack_client)
    try:
        await tracker.refresh()
    except SlackError:
        LOGGER.warning("Failed to refresh master tracker view.")


def _persist_jd_snapshot(session: Session, application: models.Application, job: models.Job) -> None:
    if not job.jd_text:
        return
    existing = (
        session.execute(
            select(models.Artifact.id)
            .where(
                models.Artifact.application_id == application.id,
                models.Artifact.type == ArtifactType.JD_SNAPSHOT,
            )
            .limit(1)
        ).scalar_one_or_none()
    )
    if existing:
        return

    artifact_dir = ARTIFACTS_DIR / application.human_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    jd_path = artifact_dir / "jd.md"
    jd_path.write_text(job.jd_text, encoding="utf-8")
    artifact = models.Artifact(
        application_id=application.id,
        type=ArtifactType.JD_SNAPSHOT,
        uri=f"file://{jd_path.resolve()}",
    )
    session.add(artifact)


def _stage_select_options() -> list[dict[str, Any]]:
    return [
        {
            "text": {"type": "plain_text", "text": stage_display(stage)},
            "value": stage.value,
        }
        for stage in TRACKER_STAGE_OPTIONS
    ]


def _uri_to_path(uri: str | None) -> Path | None:
    if not uri:
        return None
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    return Path(unquote(parsed.path))


def _load_artifact_text(
    session: Session,
    application_id: UUID,
    artifact_type: ArtifactType,
    *,
    latest: bool = True,
) -> str | None:
    stmt = (
        select(models.Artifact)
        .where(
            models.Artifact.application_id == application_id,
            models.Artifact.type == artifact_type,
        )
        .order_by(models.Artifact.created_at.desc() if latest else models.Artifact.created_at)
        .limit(1)
    )
    artifact = session.execute(stmt).scalar_one_or_none()
    if not artifact:
        return None
    path = _uri_to_path(artifact.uri)
    if not path or not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _truncate_text(text: str, max_chars: int = 2500) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _build_text_block(title: str, body: str | None) -> dict[str, Any]:
    if not body:
        content = "_Not available_"
    else:
        truncated = _truncate_text(body, 2700)
        content = f"```{truncated}```"
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"*{title}*\n{content}"},
    }


def _build_manage_view(
    application: models.Application,
    job: models.Job,
    cover_letter: str | None,
    jd_snapshot: str | None,
) -> dict[str, Any]:
    stage_name = stage_display(application.stage)
    score_line = f" · Score `{application.score:.2f}`" if application.score is not None else ""
    updated_str = application.updated_at.astimezone(timezone.utc).strftime("%b %d · %H:%M UTC")
    header_text = (
        f"*{application.human_id} — {job.title}*\n"
        f"{job.company_name} · {job.location}\n"
        f"Stage: `{stage_name}`{score_line}\n"
        f"Last updated {updated_str}\n"
        f"<{job.url}|Job link> · Canonical `{job.job_id_canonical}`"
    )

    stage_options = _stage_select_options()
    initial_stage = next((opt for opt in stage_options if opt["value"] == application.stage.value), None)

    metadata = json.dumps({"application_id": str(application.id)})

    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": header_text}},
        {
            "type": "input",
            "block_id": "stage_select_block",
            "label": {"type": "plain_text", "text": "Update stage"},
            "optional": True,
            "element": {
                "type": "static_select",
                "action_id": "stage_select",
                "options": stage_options,
                "initial_option": initial_stage,
            },
        },
        {
            "type": "actions",
            "block_id": "manage_actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Generate"},
                    "action_id": "drafts_generate",
                    "style": "primary",
                    "value": _encode_action_value({"application_id": str(application.id)}),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Finalize"},
                    "action_id": "drafts_finalize",
                    "style": "danger",
                    "value": _encode_action_value({"application_id": str(application.id)}),
                },
            ],
        },
        _build_text_block("Cover letter", cover_letter),
        _build_text_block("Job description", jd_snapshot or job.jd_text),
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Artifacts folder: `artifacts/{application.human_id}`",
                }
            ],
        },
    ]

    title_text = application.human_id[:24]
    return {
        "type": "modal",
        "callback_id": "application_stage_submit",
        "title": {"type": "plain_text", "text": title_text},
        "close": {"type": "plain_text", "text": "Close"},
        "submit": {"type": "plain_text", "text": "Save stage"},
        "private_metadata": metadata,
        "blocks": blocks,
    }


async def _post_archive_summary(
    session: Session,
    application: models.Application,
    job: models.Job,
    stage: ApplicationStage,
    slack_client: SlackClient,
    actor: str | None = None,
) -> None:
    channel_id = settings.slack_jobs_archive_channel
    if not channel_id:
        return

    stage_name = stage_display(stage)
    updated_str = application.updated_at.astimezone(timezone.utc).strftime("%b %d · %H:%M UTC")
    actor_suffix = f" by {actor}" if actor else ""
    header_text = (
        f"*{application.human_id} — {job.title}* · {job.company_name}\n"
        f"Stage: `{stage_name}`{actor_suffix}\n"
        f"Updated {updated_str}\n"
        f"<{job.url}|Job link> · `{job.job_id_canonical}`"
    )

    cover_letter = _load_artifact_text(session, application.id, ArtifactType.COVER_LETTER_VERSION)
    jd_snapshot = _load_artifact_text(session, application.id, ArtifactType.JD_SNAPSHOT)

    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": header_text}},
        _build_text_block("Cover letter", cover_letter),
        _build_text_block("Job description", jd_snapshot or job.jd_text),
    ]

    try:
        await slack_client.post_message(
            channel=channel_id,
            text=f"Archived {application.human_id} as {stage_name}",
            blocks=blocks,
        )
    except SlackError:
        LOGGER.warning("Failed to post archive summary for %s", application.human_id)


def _queue_stage_side_effects(application_id: UUID, stage: ApplicationStage, actor: str | None) -> None:
    if not settings.slack_bot_token:
        return

    async def _run() -> None:
        session = SessionLocal()
        slack_client = SlackClient(settings.slack_bot_token)
        try:
            application = session.get(models.Application, application_id)
            if not application:
                return
            job = application.job
            if stage in ARCHIVED_STAGES and job:
                await _post_archive_summary(session, application, job, stage, slack_client, actor)
            await _refresh_tracker(session, slack_client)
        except Exception:  # noqa: BLE001
            LOGGER.exception("Stage side effects failed for %s", application_id)
        finally:
            await slack_client.aclose()
            session.close()

    asyncio.create_task(_run())


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
    job_uuid, canonical_id = _parse_action_job_context(action.get("value"))
    job = None
    if job_uuid:
        job = session.get(models.Job, job_uuid)
    if job is None and canonical_id:
        job = session.execute(
            select(models.Job).where(models.Job.job_id_canonical == canonical_id)
        ).scalar_one_or_none()
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
    apply_stage(app, ApplicationStage.INTERESTED)
    session.add(app)
    session.flush()
    _persist_jd_snapshot(session, app, job)

    # Source channel/thread from the interaction payload
    source_channel_id = payload.get("channel", {}).get("id")
    source_thread_ts = (payload.get("message") or {}).get("ts")
    if not source_channel_id:
        container = payload.get("container") or {}
        source_channel_id = container.get("channel_id") or container.get("channel")
    if not source_thread_ts:
        container = payload.get("container") or {}
        source_thread_ts = container.get("thread_ts") or container.get("message_ts") or container.get("ts")
    if not source_channel_id or not source_thread_ts:
        raise SlackActionError("Missing Slack channel or thread metadata.")

    tracker_blocks = _build_thread_blocks(
        job,
        score_result.score,
        score_result.rationale,
        human_id,
        app.id,
    )
    target_channel = settings.slack_jobs_drafts_channel
    thread_anchor_ts: str | None = None

    if target_channel:
        try:
            response = await slack_client.post_message(
                channel=target_channel,
                text=f"Queued {human_id} — {job.title}",
                blocks=tracker_blocks,
            )
            target_channel = response.data.get("channel", target_channel)
            parent_ts = response.data.get("ts")
            if parent_ts:
                await slack_client.post_thread_message(
                    channel=target_channel,
                    thread_ts=parent_ts,
                    text=f"Cover-letter workspace for `{human_id}`. Drop drafts in this thread.",
                )
            thread_anchor_ts = parent_ts or source_thread_ts
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to post tracker card in drafts channel %s", target_channel)
            target_channel = None

    if not target_channel:
        # Fall back to replying in the original thread if drafts channel unavailable
        target_channel = source_channel_id
        try:
            await slack_client.post_thread_message(
                channel=target_channel,
                thread_ts=source_thread_ts,
                blocks=tracker_blocks,
            )
            thread_anchor_ts = source_thread_ts
        except Exception:  # noqa: BLE001
            thread_anchor_ts = source_thread_ts

    app.slack_channel_id = target_channel
    app.slack_thread_ts = thread_anchor_ts

    session.commit()
    await _refresh_tracker(session, slack_client)
    # If user context exists, send an ephemeral confirmation as well
    user_id = _extract_user_id(payload)
    if user_id:
        try:
            await slack_client.post_ephemeral(
                channel=source_channel_id,
                user=user_id,
                text=f"Queued `{human_id}` with score {score_result.score:.2f}.",
            )
        except Exception:
            pass
    return {"text": f"Queued `{human_id}` with score {score_result.score:.2f}."}


async def handle_application_manage_action(
    payload: dict[str, Any],
    session: Session,
    slack_client: SlackClient,
) -> dict[str, Any]:
    trigger_id = payload.get("trigger_id")
    if not trigger_id:
        raise SlackActionError("Missing trigger for tracker modal.")

    action = (payload.get("actions") or [])[0]
    application_id = _parse_application_action_value(action.get("value"))
    application = session.get(models.Application, application_id)
    if not application:
        raise SlackActionError("Application not found.")
    job = application.job
    if not job:
        raise SlackActionError("Application missing job reference.")

    cover_letter = _load_artifact_text(session, application.id, ArtifactType.COVER_LETTER_VERSION)
    jd_snapshot = _load_artifact_text(session, application.id, ArtifactType.JD_SNAPSHOT)

    view = _build_manage_view(application, job, cover_letter, jd_snapshot)
    await slack_client.open_view(trigger_id, view)
    return {"text": f"Opened `{application.human_id}`."}


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


async def handle_drafts_generate_action(
    payload: dict[str, Any],
    session: Session,
    slack_client: SlackClient,
) -> dict[str, Any]:
    action = (payload.get("actions") or [])[0]
    application_id = _parse_application_action_value(action.get("value"))
    generator = DraftGenerator(session, slack_client)
    author = _extract_user_name(payload)
    try:
        await generator.generate(application_id, notes=[], author=author, post_to_slack=True)
    except (DraftGeneratorError, LlmBackendError) as exc:
        raise SlackActionError(str(exc)) from exc
    await _refresh_tracker(session, slack_client)
    return {"text": f"Generating cover-letter draft for `{author}`."}


async def handle_drafts_finalize_action(
    payload: dict[str, Any],
    session: Session,
    slack_client: SlackClient,
) -> dict[str, Any]:
    action = (payload.get("actions") or [])[0]
    application_id = _parse_application_action_value(action.get("value"))
    generator = DraftGenerator(session, slack_client)
    author = _extract_user_name(payload)
    try:
        summary = await generator.finalize(application_id, author=author)
    except DraftGeneratorError as exc:
        raise SlackActionError(str(exc)) from exc
    await _refresh_tracker(session, slack_client)
    return {"text": f"Marked draft as ready. {summary}"}


async def handle_application_stage_submit(
    payload: dict[str, Any],
    session: Session,
    slack_client: SlackClient,
) -> dict[str, Any]:
    view = payload.get("view") or {}
    metadata_raw = view.get("private_metadata")
    if not metadata_raw:
        raise SlackActionError("Missing tracker metadata.")
    try:
        metadata = json.loads(metadata_raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise SlackActionError("Invalid tracker metadata.") from exc

    application_id_raw = metadata.get("application_id")
    if not application_id_raw:
        raise SlackActionError("Missing application identifier.")
    try:
        application_id = UUID(application_id_raw)
    except (TypeError, ValueError) as exc:
        raise SlackActionError("Malformed application identifier.") from exc

    state_values = (view.get("state") or {}).get("values") or {}
    stage_block = state_values.get("stage_select_block") or {}
    stage_state = stage_block.get("stage_select") or {}
    selected_option = stage_state.get("selected_option")
    if not selected_option:
        return {}

    stage_value = selected_option.get("value")
    if not stage_value:
        return {}

    application = session.get(models.Application, application_id)
    if not application:
        raise SlackActionError("Application not found for update.")

    try:
        new_stage = ApplicationStage(stage_value)
    except ValueError as exc:
        raise SlackActionError("Unsupported stage selection.") from exc
    if application.stage == new_stage:
        return {}

    apply_stage(application, new_stage)
    session.commit()

    actor = _extract_user_name(payload)
    _queue_stage_side_effects(application.id, new_stage, actor)
    return {"response_action": "clear"}


async def handle_interactive_request(
    payload: dict[str, Any],
    session: Session,
    slack_client: SlackClient,
) -> dict[str, Any]:
    action_type = payload.get("type")
    if action_type == "block_actions":
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
        if action_id == "drafts_generate":
            return await handle_drafts_generate_action(payload, session, slack_client)
        if action_id == "drafts_finalize":
            return await handle_drafts_finalize_action(payload, session, slack_client)
        if action_id == "application_manage":
            return await handle_application_manage_action(payload, session, slack_client)

        raise SlackActionError(f"Unknown action: {action_id}")

    if action_type == "view_submission":
        view = payload.get("view") or {}
        callback_id = view.get("callback_id")
        if callback_id == "application_stage_submit":
            return await handle_application_stage_submit(payload, session, slack_client)
        raise SlackActionError(f"Unsupported view submission: {callback_id}")

    raise SlackActionError(f"Unsupported payload type: {action_type}")
