from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

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
from agentic_jobs.services.autofill import AutofillMode, AutofillOrchestrator, AutofillError
from agentic_jobs.services.autofill.types import AutofillTaskStatus
from agentic_jobs.services.artifacts.utils import ARTIFACTS_DIR, load_artifact_text
from agentic_jobs.services.agents.coordinator import PipelineCoordinator, PipelineCoordinatorError
from agentic_jobs.services.drafts.generator import DraftGenerator, DraftGeneratorError
from agentic_jobs.services.ranking import score_job
from agentic_jobs.services.applications.stage import ARCHIVED_STAGES, apply_stage, stage_display
from agentic_jobs.services.slack.client import SlackClient, SlackError
from agentic_jobs.services.slack.tracker import MasterTracker
from agentic_jobs.services.llm.runner import LlmBackendError


LOGGER = logging.getLogger(__name__)
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
                "text": {"type": "plain_text", "text": "Quick Draft"},
                "action_id": "drafts_quick",
                "value": payload,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Generate CL"},
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


def _manage_action_buttons(application: models.Application) -> list[dict[str, Any]]:
    payload = _encode_action_value({"application_id": str(application.id)})
    buttons = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Quick Draft"},
            "action_id": "drafts_quick",
            "value": payload,
        },
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Generate CL"},
            "action_id": "drafts_generate",
            "style": "primary",
            "value": payload,
        }
    ]
    if application.stage != ApplicationStage.COVER_LETTER_FINALIZED:
        buttons.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Finalize"},
                "action_id": "drafts_finalize",
                "style": "danger",
                "value": _encode_action_value({"application_id": str(application.id)}),
            }
        )
    return buttons


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
            "elements": _manage_action_buttons(application),
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

    if settings.autofill_enabled and application.stage == ApplicationStage.COVER_LETTER_FINALIZED:
        autofill_payload = _encode_action_value({"application_id": str(application.id)})
        autofill_elements = [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Queue Application"},
                "action_id": "autofill_queue",
                "style": "primary",
                "value": autofill_payload,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Autofill Application"},
                "action_id": "autofill_start",
                "value": autofill_payload,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Autofill Queue"},
                "action_id": "autofill_run_all",
                "value": json.dumps({"source": "manage_view"}),
            },
        ]
        blocks.insert(
            3,
            {
                "type": "actions",
                "block_id": "autofill_actions",
                "elements": autofill_elements,
            },
        )

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

    cover_letter = load_artifact_text(session, application.id, ArtifactType.COVER_LETTER_VERSION)
    jd_snapshot = load_artifact_text(session, application.id, ArtifactType.JD_SNAPSHOT)

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

    task = asyncio.create_task(_run())
    # Prevent the task from being silently GC'd before it completes; log any unexpected errors.
    task.add_done_callback(
        lambda t: LOGGER.exception("Stage side-effects task raised unexpectedly", exc_info=t.exception())
        if not t.cancelled() and t.exception()
        else None
    )


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
        except Exception:  # noqa: BLE001
            LOGGER.warning("Failed to send ephemeral confirmation to user %s", user_id)
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

    cover_letter = load_artifact_text(session, application.id, ArtifactType.COVER_LETTER_VERSION)
    jd_snapshot = load_artifact_text(session, application.id, ArtifactType.JD_SNAPSHOT)

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
        domain.status = DomainReviewStatus.APPROVED
        domain.resolved_at = now_utc
        session.commit()
    except IntegrityError:
        session.rollback()
        raise SlackActionError("Failed to insert whitelist entry.")

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
    """Quick Draft — single-pass, current DraftGenerator behavior."""
    action = (payload.get("actions") or [])[0]
    application_id = _parse_application_action_value(action.get("value"))
    generator = DraftGenerator(session, slack_client)
    author = _extract_user_name(payload)
    try:
        await generator.generate(application_id, notes=[], author=author, post_to_slack=True)
    except (DraftGeneratorError, LlmBackendError) as exc:
        raise SlackActionError(str(exc)) from exc
    await _refresh_tracker(session, slack_client)
    return {"text": f"Quick draft generating for `{author}`."}


async def handle_drafts_pipeline_action(
    payload: dict[str, Any],
    session: Session,
    slack_client: SlackClient,
) -> dict[str, Any]:
    """Generate CL — full multi-agent pipeline (Researcher → Writer → HM review loop)."""
    action = (payload.get("actions") or [])[0]
    application_id = _parse_application_action_value(action.get("value"))
    author = _extract_user_name(payload)
    coordinator = PipelineCoordinator(session, slack_client)
    try:
        result = await coordinator.run(
            application_id,
            author=author,
            post_to_slack=True,
        )
    except (PipelineCoordinatorError, LlmBackendError) as exc:
        raise SlackActionError(str(exc)) from exc
    await _refresh_tracker(session, slack_client)
    final_score = result.review_history[-1].score if result.review_history else None
    score_str = f" | Score: {final_score}/10" if final_score is not None else ""
    return {"text": f"Cover letter generated for `{author}`{score_str}."}


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
    if settings.autofill_enabled:
        try:
            await _handle_autofill_action(
                payload,
                session,
                slack_client,
                mode=AutofillMode.AUTOFILL,
                enforce_stage=False,
                quiet=True,
            )
        except SlackActionError:
            LOGGER.debug("Auto-queue after finalize failed for %s", application_id)
    return {"text": f"Marked draft as ready. {summary}"}


async def _handle_autofill_action(
    payload: dict[str, Any],
    session: Session,
    slack_client: SlackClient,
    *,
    mode: AutofillMode,
    enforce_stage: bool = True,
    quiet: bool = False,
    auto_start: bool = True,
) -> dict[str, Any]:
    if not settings.autofill_enabled:
        raise SlackActionError("Autofill is disabled.")
    action = (payload.get("actions") or [])[0]
    application_id = _parse_application_action_value(action.get("value"))
    application = session.get(models.Application, application_id)
    if not application:
        raise SlackActionError("Application not found.")
    if enforce_stage and application.stage != ApplicationStage.COVER_LETTER_FINALIZED:
        raise SlackActionError("Autofill is available once the cover letter is finalized.")
    orchestrator = AutofillOrchestrator(settings)
    actor = _extract_user_name(payload)
    task_stmt = (
        select(models.AutofillTask)
        .where(models.AutofillTask.application_id == application.id)
        .order_by(models.AutofillTask.created_at.desc())
        .limit(1)
    )
    existing_task = session.execute(task_stmt).scalar_one_or_none()

    if not auto_start:
        if existing_task and existing_task.status == AutofillTaskStatus.QUEUED:
            if quiet:
                return {}
            return {"text": f"`{application.human_id}` already queued for autofill."}
    elif mode is AutofillMode.AUTOFILL and existing_task and existing_task.status == AutofillTaskStatus.QUEUED:
        if await orchestrator.run_pending_task(session, existing_task, slack_client):
            if quiet:
                return {}
            return {"text": f"Autofill started for `{application.human_id}`."}

    try:
        result = await orchestrator.queue_application(
            session,
            application,
            slack_client,
            mode=mode,
            actor=actor,
            auto_start=auto_start,
        )
    except AutofillError as exc:
        raise SlackActionError(str(exc)) from exc

    if quiet:
        return {}
    if result.status is AutofillTaskStatus.IN_PROGRESS:
        return {"text": f"Autofill started for `{application.human_id}`."}
    if result.status is AutofillTaskStatus.QUEUED:
        return {"text": f"Queued `{application.human_id}` for autofill."}
    if result.status is AutofillTaskStatus.SKIPPED:
        return {"text": result.message}
    return {"text": f"Autofill blocked: {result.message}"}


async def handle_autofill_queue_action(
    payload: dict[str, Any],
    session: Session,
    slack_client: SlackClient,
) -> dict[str, Any]:
    return await _handle_autofill_action(
        payload,
        session,
        slack_client,
        mode=AutofillMode.AUTOFILL,
        auto_start=False,
    )


async def handle_autofill_start_action(
    payload: dict[str, Any],
    session: Session,
    slack_client: SlackClient,
) -> dict[str, Any]:
    return await _handle_autofill_action(
        payload,
        session,
        slack_client,
        mode=AutofillMode.AUTOFILL,
        auto_start=True,
    )


async def handle_autofill_open_tabs_action(
    payload: dict[str, Any],
    session: Session,
    slack_client: SlackClient,
) -> dict[str, Any]:
    return await _handle_autofill_action(payload, session, slack_client, mode=AutofillMode.OPEN_TABS)


async def handle_autofill_run_all_action(
    payload: dict[str, Any],
    session: Session,
    slack_client: SlackClient,
) -> dict[str, Any]:
    if not settings.autofill_enabled:
        raise SlackActionError("Autofill is disabled.")
    tasks = list(
        session.execute(
            select(models.AutofillTask)
            .where(models.AutofillTask.status == AutofillTaskStatus.QUEUED)
            .order_by(models.AutofillTask.created_at)
        ).scalars()
    )
    if not tasks:
        return {"text": "No queued applications to run."}
    orchestrator = AutofillOrchestrator(settings)
    launched = 0
    for task in tasks:
        try:
            if await orchestrator.run_pending_task(session, task, slack_client):
                launched += 1
        except AutofillError:
            LOGGER.exception("Failed to start autofill task %s", task.id)
    if launched == 0:
        return {"text": "No queued applications available to launch."}
    return {"text": f"Launching {launched} queued application(s)."}


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
        if action_id == "drafts_quick":
            return await handle_drafts_generate_action(payload, session, slack_client)
        if action_id == "drafts_generate":
            return await handle_drafts_pipeline_action(payload, session, slack_client)
        if action_id == "drafts_finalize":
            return await handle_drafts_finalize_action(payload, session, slack_client)
        if action_id == "application_manage":
            return await handle_application_manage_action(payload, session, slack_client)
        if action_id == "autofill_queue":
            return await handle_autofill_queue_action(payload, session, slack_client)
        if action_id == "autofill_start":
            return await handle_autofill_start_action(payload, session, slack_client)
        if action_id == "autofill_open_tabs":
            return await handle_autofill_open_tabs_action(payload, session, slack_client)
        if action_id == "autofill_run_all":
            return await handle_autofill_run_all_action(payload, session, slack_client)

        raise SlackActionError(f"Unknown action: {action_id}")

    if action_type == "view_submission":
        view = payload.get("view") or {}
        callback_id = view.get("callback_id")
        if callback_id == "application_stage_submit":
            return await handle_application_stage_submit(payload, session, slack_client)
        raise SlackActionError(f"Unsupported view submission: {callback_id}")

    raise SlackActionError(f"Unsupported payload type: {action_type}")
