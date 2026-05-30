from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from datetime import datetime, time, timedelta, timezone

from zoneinfo import ZoneInfo

from agentic_jobs.config import settings
from agentic_jobs.db.session import SessionLocal
from agentic_jobs.services.discovery.config import get_job_filter_config
from agentic_jobs.services.discovery.github_adapter import GithubPositionsAdapter
from agentic_jobs.services.discovery.greenhouse_adapter import GreenhouseAdapter
from agentic_jobs.services.discovery.orchestrator import run_discovery
from agentic_jobs.services.discovery.universal.adapter import UniversalAdapter
from agentic_jobs.services.discovery.universal.sites_config import load_universal_sites_config
from agentic_jobs.services.slack.client import SlackClient, SlackError
from agentic_jobs.services.slack.digest import build_digest_blocks, build_needs_review_blocks
from agentic_jobs.services.slack.workflows import (
    collect_digest_rows,
    collect_needs_review_candidates,
    last_posted_job_scraped_at,
    record_digest_post,
)

LOGGER = logging.getLogger(__name__)
PT_ZONE = ZoneInfo(settings.scheduler_timezone)
# Tolerance subtracted from interval checks so a run that fires slightly early
# isn't skipped on the next tick.
_SCHEDULE_GUARD = timedelta(minutes=30)
_scheduler_task: asyncio.Task | None = None
_last_run_at_utc: datetime | None = None
_last_memory_assess_utc: datetime | None = None
_last_vault_refresh_utc: datetime | None = None


def _schedule_hours() -> list[int]:
    # Determine hours within the window at the configured interval
    interval = max(1, int(getattr(settings, "discovery_interval_hours", 3)))
    return list(range(settings.scheduler_window_start_hour_pt, settings.scheduler_window_end_hour_pt + 1, interval))


def _next_run_time(now_pt: datetime) -> datetime:
    # Compute the next aligned time at the configured hour interval within the window
    interval = max(1, int(getattr(settings, "discovery_interval_hours", 3)))
    start_hour = settings.scheduler_window_start_hour_pt
    end_hour = settings.scheduler_window_end_hour_pt
    if end_hour < start_hour:
        # Misconfigured window — skip to next day at start_hour rather than looping forever
        next_day = now_pt.date() + timedelta(days=1)
        return datetime.combine(next_day, time(hour=start_hour, minute=0, second=0), tzinfo=PT_ZONE)
    # Align to the next interval boundary
    current_hour = now_pt.hour
    # Find the smallest h >= current_hour that satisfies (h - start) % interval == 0
    candidate_hour = current_hour
    while True:
        if start_hour <= candidate_hour <= end_hour and ((candidate_hour - start_hour) % interval == 0):
            candidate = now_pt.replace(hour=candidate_hour, minute=0, second=0, microsecond=0)
            if candidate >= now_pt - _SCHEDULE_GUARD:
                return candidate
        candidate_hour += 1
        if candidate_hour > end_hour:
            # Move to next day at the first interval
            next_day = now_pt.date() + timedelta(days=1)
            return datetime.combine(next_day, time(hour=start_hour, minute=0, second=0), tzinfo=PT_ZONE)


async def _run_discovery_cycle(run_started: datetime) -> None:
    async with AsyncExitStack() as stack:
        adapters: list = []
        filter_config = get_job_filter_config(settings.job_filter_config_path)

        if settings.enable_greenhouse and filter_config.adapters.get("greenhouse", True):
            greenhouse = await stack.enter_async_context(GreenhouseAdapter(settings))
            adapters.append(greenhouse)

        if filter_config.adapters.get("simplify", True):
            simplify = await stack.enter_async_context(
                GithubPositionsAdapter(
                    settings,
                    source_name="simplify",
                    slug="simplify",
                    data_urls=settings.simplify_positions_url_list,
                    display_name="GitHub · Simplify",
                )
            )
            adapters.append(simplify)

        if filter_config.adapters.get("newgrad2026", True):
            new_grad = await stack.enter_async_context(
                GithubPositionsAdapter(
                    settings,
                    source_name="newgrad2026",
                    slug="newgrad2026",
                    data_urls=settings.new_grad_positions_url_list,
                    display_name="GitHub · NewGrad2026",
                )
            )
            adapters.append(new_grad)

        if filter_config.adapters.get("universal", True):
            universal_sites = load_universal_sites_config(settings.universal_sites_config_path)
            if universal_sites.feeds:
                universal = await stack.enter_async_context(
                    UniversalAdapter(settings, sites_config=universal_sites)
                )
                adapters.append(universal)

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
    last_posted_at = last_posted_job_scraped_at(session)

    digest_rows = collect_digest_rows(
        session,
        since=last_posted_at,
        digest_day=digest_day,
        limit=settings.digest_batch_size,
    )

    candidates = collect_needs_review_candidates(session, since=run_started)

    if not settings.slack_bot_token or not settings.slack_jobs_feed_channel:
        if digest_rows or candidates:
            LOGGER.info("Slack not configured; skipping message post for this cycle.")
        else:
            LOGGER.info("Slack not configured; cannot post empty digest notification.")
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
        else:
            LOGGER.info("No new postings for %s; sending empty digest notice.", digest_day)
            try:
                await slack_client.post_message(
                    channel=settings.slack_jobs_feed_channel,
                    text=f"{digest_day.strftime('%b %d')} digest — no new postings",
                    blocks=build_digest_blocks([]),
                )
            except SlackError as exc:
                LOGGER.exception("Failed to post no-new-roles digest: %s", exc)

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


async def _memory_assess_job() -> None:
    """
    Condense accumulated user feedback into long-term learnings.
    Runs every N days (default: 3) as configured by MEMORY_ASSESSMENT_INTERVAL_DAYS.

    Flow:
      1. Load all ApplicationFeedback(role=USER) notes since last assessment
      2. Filter noise (too short, ack phrases)
      3. Truncate each to 200 chars
      4. Batch to LLM: extract generalizable learnings
      5. Save extracted learnings as AgentMemory(type=LONG_TERM, source=auto_assessed)
    """
    global _last_memory_assess_utc
    interval_days = settings.memory_assessment_interval_days
    now = datetime.now(tz=timezone.utc)

    if _last_memory_assess_utc is not None:
        if (now - _last_memory_assess_utc) < timedelta(days=interval_days) - _SCHEDULE_GUARD:
            return

    LOGGER.info("Running memory auto-assess job")
    session = SessionLocal()
    try:
        from agentic_jobs.services.memory.store import MemoryStore
        store = MemoryStore(session)
        count = await store.auto_assess()
        LOGGER.info("Memory auto-assess complete: %d learnings extracted", count)
        _last_memory_assess_utc = now
    except Exception:  # noqa: BLE001
        LOGGER.exception("Memory auto-assess job failed.")
    finally:
        session.close()


async def _vault_refresh_job() -> None:
    """
    Re-embed any vault sections whose source file has changed since last embed.
    Runs every 12 hours. Skips gracefully if vault path is not configured or
    the embedding endpoint is unreachable.
    """
    global _last_vault_refresh_utc
    now = datetime.now(tz=timezone.utc)

    if _last_vault_refresh_utc is not None:
        if (now - _last_vault_refresh_utc) < timedelta(hours=settings.vault_refresh_interval_hours):
            return

    if not settings.vault_path:
        return

    LOGGER.info("Running vault embedding refresh job")
    session = SessionLocal()
    try:
        from pathlib import Path
        from agentic_jobs.services.vault.embedder import VaultEmbedder
        from agentic_jobs.services.vault.parser import VaultParser

        parser = VaultParser(Path(settings.vault_path))
        sections = parser.parse_all()
        embedder = VaultEmbedder(session)

        if not await VaultEmbedder.health_check():
            LOGGER.warning("Vault refresh: embedding endpoint unreachable, skipping.")
            return

        refreshed = await embedder.refresh_stale(sections)
        LOGGER.info("Vault refresh: %d sections updated", refreshed)
        _last_vault_refresh_utc = now
    except Exception:  # noqa: BLE001
        LOGGER.exception("Vault refresh job failed.")
    finally:
        session.close()


async def scheduler_job() -> None:
    global _last_run_at_utc
    start_hour = settings.scheduler_window_start_hour_pt
    end_hour = settings.scheduler_window_end_hour_pt
    if end_hour < start_hour:
        LOGGER.error(
            "Scheduler window is misconfigured: start_hour=%d > end_hour=%d — "
            "discovery will never run. Fix SCHEDULER_WINDOW_START_HOUR_PT / END_HOUR_PT.",
            start_hour, end_hour,
        )
        return
    now_pt = datetime.now(tz=PT_ZONE)
    if not (start_hour <= now_pt.hour <= end_hour):
        LOGGER.info("Current time outside scheduler window: %s", now_pt.isoformat())
        return

    LOGGER.info("Running scheduled discovery + digest cycle at %s", now_pt.isoformat())
    run_started = datetime.now(tz=timezone.utc)
    try:
        await _run_discovery_cycle(run_started)
        _last_run_at_utc = run_started
    except Exception:  # noqa: BLE001
        LOGGER.exception("Scheduled cycle failed.")


async def _scheduler_loop() -> None:
    # Run immediately on startup if within window and hasn't run recently
    await scheduler_job()
    # Run memory + vault jobs on startup (they are internally gated by their own intervals)
    await _memory_assess_job()
    await _vault_refresh_job()
    while True:
        now_pt = datetime.now(tz=PT_ZONE)
        target = _next_run_time(now_pt)
        sleep_seconds = max((target - now_pt).total_seconds(), 0)
        await asyncio.sleep(sleep_seconds)
        # Ensure interval gating (avoid duplicate runs if woke up early)
        if _last_run_at_utc is not None:
            interval_hours = max(1, int(getattr(settings, "discovery_interval_hours", 3)))
            if (datetime.now(tz=timezone.utc) - _last_run_at_utc) < timedelta(hours=interval_hours) - _SCHEDULE_GUARD:
                continue
        await scheduler_job()
        # These are internally gated — safe to call every discovery cycle
        await _memory_assess_job()
        await _vault_refresh_job()


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
