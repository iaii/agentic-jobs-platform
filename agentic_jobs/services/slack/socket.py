from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.web.async_client import AsyncWebClient

from agentic_jobs.config import settings
from agentic_jobs.db.session import SessionLocal
from agentic_jobs.services.slack.actions import handle_interactive_request
from agentic_jobs.services.slack.client import SlackClient, SlackError

LOGGER = logging.getLogger(__name__)

_socket_client: SocketModeClient | None = None


async def _process_interaction(payload: dict[str, Any]) -> None:
    session = SessionLocal()
    slack_client = SlackClient(settings.slack_bot_token)

    try:
        await handle_interactive_request(payload, session, slack_client)
    except SlackError as exc:
        LOGGER.warning("Interactive handler failed: %s", exc)
    except Exception:  # noqa: BLE001
        LOGGER.exception("Failed to process Slack interaction payload.")
    finally:
        await slack_client.aclose()
        session.close()


async def _handle_socket_request(client: SocketModeClient, req: SocketModeRequest) -> None:
    if req.type != "interactive":
        await client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        return

    payload_raw = req.payload.get("payload")
    if not payload_raw:
        LOGGER.warning("Received interactive request without payload.")
        await client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        return

    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        LOGGER.warning("Invalid payload JSON from Slack: %s", payload_raw)
        await client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        return

    await client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
    asyncio.create_task(_process_interaction(payload))


async def start_socket_mode() -> None:
    global _socket_client

    if _socket_client or not settings.slack_bot_token or not settings.slack_app_level_token:
        return

    web_client = AsyncWebClient(token=settings.slack_bot_token)
    _socket_client = SocketModeClient(
        app_token=settings.slack_app_level_token,
        web_client=web_client,
    )
    _socket_client.socket_mode_request_listeners.append(_handle_socket_request)

    try:
        await _socket_client.connect()
        LOGGER.info("Slack socket mode client connected.")
    except Exception:  # noqa: BLE001
        LOGGER.exception("Failed to connect Slack socket mode client.")
        await web_client.close()
        _socket_client = None


async def stop_socket_mode() -> None:
    global _socket_client
    if not _socket_client:
        return
    try:
        await _socket_client.close()
        await _socket_client.web_client.close()
        LOGGER.info("Slack socket mode client disconnected.")
    except Exception:  # noqa: BLE001
        LOGGER.exception("Error while shutting down Slack socket mode client.")
    finally:
        _socket_client = None
