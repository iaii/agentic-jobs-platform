from __future__ import annotations

import json
from json import JSONDecodeError

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from agentic_jobs.config import settings
from agentic_jobs.db.session import get_session
from agentic_jobs.services.slack.actions import (
    SlackActionError,
    handle_interactive_request,
)
from agentic_jobs.services.slack.client import SlackClient, SlackError

router = APIRouter()


def _ensure_slack_configured() -> None:
    if not settings.slack_bot_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Slack integration is not configured.",
        )


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

    client = SlackClient(settings.slack_bot_token)
    try:
        response = await handle_interactive_request(payload, db, client)
    except SlackActionError as exc:
        # Friendly message back to Slack; keep 200 so the client doesn't show a red error
        return {"text": str(exc)}
    except SlackError as exc:
        # Convert Slack network/API errors into a friendly ephemeral message to avoid Slack error badge
        return {"text": f"Temporary Slack error: {str(exc)}"}
    finally:
        await client.aclose()

    return response
