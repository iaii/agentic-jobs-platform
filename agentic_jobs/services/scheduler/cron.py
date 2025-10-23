from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from datetime import datetime, time, timedelta, timezone

from zoneinfo import ZoneInfo

from agentic_jobs.config import settings
from agentic_jobs.db.session import SessionLocal
from agentic_jobs.services.discovery.github_adapter import GithubPositionsAdapter
from agentic_jobs.services.discovery.greenhouse_adapter import GreenhouseAdapter
from agentic_jobs.services.discovery.orchestrator import run_discovery
from agentic_jobs.services.slack.client import SlackClient, SlackError
from agentic_jobs.services.slack.digest import build_digest_blocks, build_needs_review_blocks
from agentic_jobs.services.slack.workflows import (
    collect_digest_rows,
    collect_needs_review_candidates,
    record_digest_post,
)

LOGGER = logging.getLogger(__name__)
PT_ZONE = ZoneInfo("America/Los_Angeles")
_scheduler_task: asyncio.Task | None = None


def _schedule_hours() -> list[int]:
    return list(range(settings.scheduler_window_start_hour_pt, settings.scheduler_window_end_hour_pt + 1, 3))


def _next_run_time(now_pt: datetime) -> datetime:
    hours = _schedule_hours()
    for hour in hours:
        candidate = now_pt.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate >= now_pt - timedelta(minutes=1):
            return candidate

    next_day = now_pt.date() + timedelta(days=1)
    return datetime.combine(next_day, time(hour=hours[0], minute=0, second=0), tzinfo=PT_ZONE)


async def _run_discovery_cycle(run_started: datetime) -> None:
    async with AsyncExitStack() as stack:
        adapters: list = []
        if settings.enable_greenhouse:
            greenhouse = await stack.enter_async_context(GreenhouseAdapter(settings))
            adapters.append(greenhouse)

        simplify = await stack.enter_async_context(
            GithubPositionsAdapter(
                settings,
                source_name="simplify",
                slug="simplify",
                data_urls=settings.simplify_positions_url_list,
            )
        )
        adapters.append(simplify)

        new_grad = await stack.enter_async_context(
            GithubPositionsAdapter(
                settings,
                source_name="newgrad2026",
                slug="newgrad2026",
                data_urls=settings.new_grad_positions_url_list,
            )
        )
        adapters.append(new_grad)

        session = SessionLocal()
        try:
            summary = await run_discovery(session, adapters, settings)
            LOGGER.info(
                "Discovery summary: orgs=%s jobs_seen=%s jobs_inserted=%s domains=%s",
                summary.orgs_crawled,
                summary.jobs_seen,
                summary.jobs_inserted,
                summary.domains_scored,
            )
            await _post_digest_and_reviews(session, run_started)
        finally:
            session.close()


async def _post_digest_and_reviews(session, run_started: datetime) -> None:
    digest_day = datetime.now(tz=PT_ZONE).date()

    digest_rows = collect_digest_rows(
        session,
        since=run_started,
        digest_day=digest_day,
        limit=settings.digest_batch_size,
    )

    candidates = collect_needs_review_candidates(session, since=run_started)

    if not settings.slack_bot_token or not settings.slack_jobs_feed_channel:
        if digest_rows or candidates:
            LOGGER.info("Slack not configured; skipping message post for this cycle.")
        return

    if not digest_rows and not candidates:
        LOGGER.info("No digest rows or needs-review candidates for this cycle.")
        return

    async with SlackClient(settings.slack_bot_token) as slack_client:
        if digest_rows:
            header = f"{digest_day.strftime('%b %d')} digest — {len(digest_rows)} roles"
            try:
                response = await slack_client.post_message(
                    channel=settings.slack_jobs_feed_channel,
                    text=header,
                    blocks=build_digest_blocks(digest_rows),
                )
            except SlackError as exc:
                LOGGER.exception("Failed to post digest: %s", exc)
            else:
                channel_id = response.data.get("channel", settings.slack_jobs_feed_channel)
                message_ts = response.data.get("ts", "")
                record_digest_post(
                    session,
                    rows=digest_rows,
                    digest_day=digest_day,
                    channel_id=channel_id,
                    message_ts=message_ts,
                )

        for candidate in candidates:
            try:
                response = await slack_client.post_message(
                    channel=settings.slack_jobs_feed_channel,
                    text=f"Needs review: {candidate.card.domain_root}",
                    blocks=build_needs_review_blocks(candidate.card),
                )
            except SlackError as exc:
                LOGGER.exception("Failed to post needs-review card for %s: %s", candidate.card.domain_root, exc)
                continue
            candidate.record.slack_channel_id = response.data.get("channel")
            candidate.record.slack_message_ts = response.data.get("ts")
        if candidates:
            session.commit()


async def scheduler_job() -> None:
    now_pt = datetime.now(tz=PT_ZONE)
    if not (_schedule_hours()[0] <= now_pt.hour <= settings.scheduler_window_end_hour_pt):
        LOGGER.info("Current time outside scheduler window: %s", now_pt.isoformat())
        return

    LOGGER.info("Running scheduled discovery + digest cycle at %s", now_pt.isoformat())
    run_started = datetime.now(tz=timezone.utc)
    try:
        await _run_discovery_cycle(run_started)
    except Exception:  # noqa: BLE001
        LOGGER.exception("Scheduled cycle failed.")


async def _scheduler_loop() -> None:
    while True:
        now_pt = datetime.now(tz=PT_ZONE)
        target = _next_run_time(now_pt)
        sleep_seconds = max((target - now_pt).total_seconds(), 0)
        await asyncio.sleep(sleep_seconds)
        await scheduler_job()


def start_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task:
        return

    if settings.environment.lower() == "test":
        LOGGER.info("Test environment detected; scheduler not started.")
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        LOGGER.warning("No running event loop; scheduler not started.")
        return

    _scheduler_task = loop.create_task(_scheduler_loop(), name="jobs-scheduler")
    LOGGER.info("Scheduler started with runs at PT hours: %s", ",".join(str(h) for h in _schedule_hours()))


async def shutdown_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task:
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
        _scheduler_task = None
        LOGGER.info("Scheduler stopped.")
