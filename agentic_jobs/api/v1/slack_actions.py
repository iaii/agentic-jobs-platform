from __future__ import annotations

import json
from json import JSONDecodeError
import asyncio
import httpx

from fastapi import APIRouter, Depends, HTTPException, Request, status
import logging
from sqlalchemy.orm import Session

from agentic_jobs.config import settings
from agentic_jobs.db.session import get_session
from agentic_jobs.services.slack.actions import (
    SlackActionError,
    handle_interactive_request,
)
from agentic_jobs.services.slack.client import SlackClient, SlackError

LOGGER = logging.getLogger(__name__)
router = APIRouter()


def _ensure_slack_configured() -> None:
    if not settings.slack_bot_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Slack integration is not configured.",
        )


async def _process_interactive_http(payload: dict, db: Session) -> None:
    response_url = payload.get("response_url")
    client = SlackClient(settings.slack_bot_token)
    try:
        result = await handle_interactive_request(payload, db, client)
        if response_url and isinstance(result, dict) and result.get("text"):
            try:
                verify_param: Any = True
                try:
                    import certifi  # type: ignore
                    verify_param = certifi.where()
                except Exception:
                    verify_param = True
                async with httpx.AsyncClient(timeout=5.0, verify=verify_param) as http_client:
                    await http_client.post(
                        response_url,
                        json={
                            "response_type": "ephemeral",
                            "replace_original": False,
                            "text": result["text"],
                        },
                    )
            except Exception:
                pass
    except SlackError as exc:
        if response_url:
            try:
                verify_param: Any = True
                try:
                    import certifi  # type: ignore
                    verify_param = certifi.where()
                except Exception:
                    verify_param = True
                async with httpx.AsyncClient(timeout=5.0, verify=verify_param) as http_client:
                    await http_client.post(
                        response_url,
                        json={
                            "response_type": "ephemeral",
                            "replace_original": False,
                            "text": f"Action failed: {exc}",
                        },
                    )
            except Exception:
                pass
    finally:
        await client.aclose()


@router.post(
    "/interactive",
    status_code=status.HTTP_200_OK,
)
async def slack_interactive_endpoint(
    request: Request,
    db: Session = Depends(get_session),
) -> dict:
    _ensure_slack_configured()

    form = await request.form()
    payload_raw = form.get("payload")
    if not payload_raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing payload.")

    try:
        payload = json.loads(payload_raw)
    except JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload.") from exc

    # Respond immediately to avoid Slack error badge; process in background
    LOGGER.info("/slack/interactive payload accepted; scheduling background processing")
    asyncio.create_task(_process_interactive_http(payload, db))
    return {}
