import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from agentic_jobs.core.enums import DomainReviewStatus
from agentic_jobs.db import models
from agentic_jobs.services.slack.actions import (
    handle_needs_review_approve,
    handle_needs_review_reject,
)
from agentic_jobs.services.slack.client import SlackResponse


class DummySlackClient:
    def __init__(self) -> None:
        self.updated_messages: list[dict] = []

    async def update_message(self, channel: str, ts: str, *, blocks=None, text=None):
        self.updated_messages.append({"channel": channel, "ts": ts, "blocks": blocks, "text": text})
        return SlackResponse(ok=True, data={})


@pytest.fixture
def pending_domain(sqlite_session):
    domain = models.DomainReview(
        domain_root="example.com",
        status=DomainReviewStatus.PENDING,
        company_name="Acme",
        ats_type="greenhouse",
    )
    sqlite_session.add(domain)
    sqlite_session.commit()
    return domain


def test_handle_needs_review_approve_adds_whitelist(sqlite_session, pending_domain):
    payload = {
        "type": "block_actions",
        "user": {"id": "U123"},
        "channel": {"id": "C123"},
        "message": {"ts": "1700000000.000000"},
        "actions": [
            {
                "action_id": "needs_review_approve",
                "value": pending_domain.domain_root,
            }
        ],
    }

    client = DummySlackClient()
    response = asyncio.run(handle_needs_review_approve(payload, sqlite_session, client))

    whitelist = sqlite_session.get(models.Whitelist, pending_domain.domain_root)
    updated_domain = sqlite_session.execute(
        select(models.DomainReview).where(models.DomainReview.domain_root == pending_domain.domain_root)
    ).scalar_one()

    assert "Approved" in response["text"]
    assert whitelist is not None
    assert updated_domain.status is DomainReviewStatus.APPROVED
    assert client.updated_messages


def test_handle_needs_review_reject_sets_mute(sqlite_session, pending_domain):
    payload = {
        "type": "block_actions",
        "user": {"id": "U456"},
        "channel": {"id": "C123"},
        "message": {"ts": "1700000001.000000"},
        "actions": [
            {
                "action_id": "needs_review_reject",
                "value": pending_domain.domain_root,
            }
        ],
    }

    client = DummySlackClient()
    response = asyncio.run(handle_needs_review_reject(payload, sqlite_session, client, mute_days=3))

    updated_domain = sqlite_session.execute(
        select(models.DomainReview).where(models.DomainReview.domain_root == pending_domain.domain_root)
    ).scalar_one()

    assert "Muted" in response["text"] or "muted" in response["text"].lower()
    assert updated_domain.status is DomainReviewStatus.MUTED
    assert updated_domain.muted_until is not None
    muted_until = updated_domain.muted_until
    now_utc = datetime.now(tz=timezone.utc)
    if muted_until.tzinfo is None:
        delta_days = (muted_until - now_utc.replace(tzinfo=None)).days
    else:
        delta_days = (muted_until - now_utc).days
    assert delta_days <= 3
    assert client.updated_messages
