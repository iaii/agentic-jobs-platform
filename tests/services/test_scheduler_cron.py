from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agentic_jobs.services.scheduler import cron


def _make_settings(**overrides):
    defaults = {
        "discovery_interval_hours": 3,
        "scheduler_window_start_hour_pt": 7,
        "scheduler_window_end_hour_pt": 19,
        "slack_bot_token": "xoxb-test",
        "slack_jobs_feed_channel": "C123",
        "digest_batch_size": 10,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_schedule_hours_every_three_hours(monkeypatch):
    monkeypatch.setattr(
        cron,
        "settings",
        _make_settings(scheduler_window_start_hour_pt=6, scheduler_window_end_hour_pt=18),
    )
    assert cron._schedule_hours() == [6, 9, 12, 15, 18]


def test_next_run_time_aligns_to_three_hour_grid(monkeypatch):
    monkeypatch.setattr(
        cron,
        "settings",
        _make_settings(scheduler_window_start_hour_pt=6, scheduler_window_end_hour_pt=18),
    )
    now = datetime(2024, 7, 10, 9, 30, tzinfo=cron.PT_ZONE)
    target = cron._next_run_time(now)
    assert target.hour == 12
    assert target.minute == 0
    assert target.date() == now.date()


def test_next_run_time_rolls_to_next_day(monkeypatch):
    monkeypatch.setattr(
        cron,
        "settings",
        _make_settings(scheduler_window_start_hour_pt=6, scheduler_window_end_hour_pt=18),
    )
    now = datetime(2024, 7, 10, 19, 0, tzinfo=cron.PT_ZONE)
    target = cron._next_run_time(now)
    assert target.date() == (now + timedelta(days=1)).date()
    assert target.hour == 6


def test_post_digest_posts_empty_message_when_no_jobs(monkeypatch, sqlite_session):
    recorded_messages: list[dict] = []

    class DummySlackClient:
        def __init__(self, _token: str) -> None:
            pass

        async def __aenter__(self) -> "DummySlackClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post_message(self, *, channel: str, text: str, blocks):
            recorded_messages.append({"channel": channel, "text": text, "blocks": blocks})
            return SimpleNamespace(data={"channel": channel, "ts": "1700000000.000000"})

    async def _run() -> None:
        dummy_settings = _make_settings()
        monkeypatch.setattr(cron, "settings", dummy_settings)
        monkeypatch.setattr(cron, "SlackClient", DummySlackClient)
        await cron._post_digest_and_reviews(sqlite_session, datetime.now(tz=timezone.utc))

    asyncio.run(_run())

    assert recorded_messages
    message = recorded_messages[0]
    assert "no new postings" in message["text"]
    assert message["blocks"][0]["text"]["text"] == "No new roles matched this cycle."
