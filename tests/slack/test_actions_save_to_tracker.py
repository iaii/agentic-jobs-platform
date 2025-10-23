import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from agentic_jobs.core.enums import JobSourceType, SubmissionMode
from agentic_jobs.db import models
from agentic_jobs.services.slack.actions import handle_save_to_tracker
from agentic_jobs.services.slack.client import SlackResponse


class DummySlackClient:
    def __init__(self) -> None:
        self.thread_calls: list[dict] = []

    async def post_thread_message(self, channel: str, thread_ts: str, *, blocks=None, text=None):
        self.thread_calls.append(
            {
                "channel": channel,
                "thread_ts": thread_ts,
                "blocks": blocks,
                "text": text,
            }
        )
        return SlackResponse(ok=True, data={"ts": "1700000000.123456", "channel": channel})


def test_handle_save_to_tracker_creates_application(sqlite_session):
    job = models.Job(
        id=uuid4(),
        title="Backend SWE",
        company_name="Acme Corp",
        location="Remote",
        url="https://example.com/job",
        source_type=JobSourceType.GREENHOUSE,
        domain_root="example.com",
        submission_mode=SubmissionMode.ATS,
        jd_text="This is a new grad backend role.",
        requirements=[],
        job_id_canonical="GH:12345",
        scraped_at=datetime.now(tz=timezone.utc),
        hash="hash123",
    )
    sqlite_session.add(job)
    sqlite_session.commit()

    payload = {
        "type": "block_actions",
        "user": {"id": "U123"},
        "channel": {"id": "C123"},
        "message": {"ts": "1700000000.000000"},
        "actions": [
            {
                "action_id": "save_to_tracker",
                "value": str(job.id),
            }
        ],
    }

    client = DummySlackClient()
    response = asyncio.run(handle_save_to_tracker(payload, sqlite_session, client))

    application = sqlite_session.execute(select(models.Application)).scalar_one()

    assert "APP-" in response["text"]
    assert application.human_id.startswith("APP-")
    assert application.slack_channel_id == "C123"
    assert application.slack_thread_ts == "1700000000.123456"
    assert client.thread_calls
