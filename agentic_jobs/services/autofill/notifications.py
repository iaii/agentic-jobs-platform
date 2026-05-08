from __future__ import annotations

import logging

from agentic_jobs.config import settings
from agentic_jobs.services.slack.client import SlackClient, SlackError


LOGGER = logging.getLogger(__name__)


async def post_ops_update(slack_client: SlackClient, *, text: str) -> None:
    channel = settings.autofill_ops_channel
    if not channel:
        return
    try:
        await slack_client.post_message(channel=channel, text=text)
    except SlackError:
        LOGGER.warning("Failed to post autofill update to channel %s", channel)
