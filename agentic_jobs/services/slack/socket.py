from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
import httpx
import ssl
import aiohttp
import os

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
    response_url = payload.get("response_url")

    try:
        result = await handle_interactive_request(payload, session, slack_client)
        if response_url and isinstance(result, dict) and result.get("text"):
            try:
                async with httpx.AsyncClient(timeout=5.0) as http_client:
                    await http_client.post(
                        response_url,
                        json={
                            "response_type": "ephemeral",
                            "replace_original": False,
                            "text": result["text"],
                        },
                    )
            except Exception:  # noqa: BLE001
                LOGGER.warning("Failed to send ephemeral response to Slack response_url.")
    except SlackError as exc:
        LOGGER.warning("Interactive handler failed: %s", exc)
        if response_url:
            try:
                async with httpx.AsyncClient(timeout=5.0) as http_client:
                    await http_client.post(
                        response_url,
                        json={
                            "response_type": "ephemeral",
                            "replace_original": False,
                            "text": f"Action failed: {exc}",
                        },
                    )
            except Exception:  # noqa: BLE001
                LOGGER.debug("Failed to send error ephemeral response.")
    except Exception:  # noqa: BLE001
        LOGGER.exception("Failed to process Slack interaction payload.")
    finally:
        await slack_client.aclose()
        session.close()


async def _handle_socket_request(client: SocketModeClient, req: SocketModeRequest) -> None:
    if req.type != "interactive":
        await client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        return

    # Immediately ACK to avoid Slack client error symbol if processing takes time
    await client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))

    # Slack SDK versions differ: sometimes the interactive payload is a JSON string
    # under key "payload"; sometimes req.payload already contains the dict.
    payload_container = req.payload.get("payload", req.payload)
    if payload_container is None:
        LOGGER.warning("Received interactive request without payload.")
        return

    if isinstance(payload_container, str):
        try:
            payload = json.loads(payload_container)
        except json.JSONDecodeError:
            LOGGER.warning("Invalid payload JSON from Slack: %s", payload_container)
            return
    elif isinstance(payload_container, dict):
        payload = payload_container
    else:
        LOGGER.warning("Unsupported payload container type: %s", type(payload_container))
        return

    LOGGER.info("Socket interactive payload accepted; scheduling background processing")
    asyncio.create_task(_process_interaction(payload))


async def start_socket_mode() -> None:
    global _socket_client

    if _socket_client:
        return
    if not settings.socket_mode_enabled:
        LOGGER.info("Socket mode disabled via settings.")
        return
    if not settings.slack_bot_token or not settings.slack_app_level_token:
        LOGGER.info("Socket mode not started: missing Slack tokens")
        return

    # Create Slack AsyncWebClient with certifi-backed SSL context to avoid local CA issues
    try:
        import certifi  # type: ignore

        # Ensure Python and HTTP libs use certifi CA bundle for TLS (affects aiohttp websockets too)
        ca_path = certifi.where()
        os.environ.setdefault("SSL_CERT_FILE", ca_path)
        os.environ.setdefault("REQUESTS_CA_BUNDLE", ca_path)

        ssl_context = ssl.create_default_context(cafile=ca_path)
        aiohttp_session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context))
        web_client = AsyncWebClient(token=settings.slack_bot_token, session=aiohttp_session)
    except Exception:  # noqa: BLE001
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
