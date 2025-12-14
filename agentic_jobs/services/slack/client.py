from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping, Optional

import httpx
import ssl
import certifi


class SlackError(RuntimeError):
    """Raised when a Slack API call fails."""


@dataclass(slots=True)
class SlackResponse:
    ok: bool
    data: dict[str, Any]


class SlackClient:
    """Thin wrapper around the Slack Web API."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://slack.com/api",
        timeout: float = 10.0,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        self._owns_client = client is None
        if client is not None:
            self._client = client
        else:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            self._client = httpx.AsyncClient(
                base_url=base_url,
                timeout=timeout,
                headers=headers,
                verify=ssl_context,
            )

    async def __aenter__(self) -> "SlackClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def post_message(
        self,
        channel: str,
        *,
        text: str | None = None,
        blocks: list[Mapping[str, Any]] | None = None,
        thread_ts: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> SlackResponse:
        payload: MutableMapping[str, Any] = {"channel": channel}
        if text is not None:
            payload["text"] = text
        if blocks is not None:
            payload["blocks"] = blocks
        if thread_ts is not None:
            payload["thread_ts"] = thread_ts
        if metadata is not None:
            payload["metadata"] = metadata
        return await self._call("chat.postMessage", payload)

    async def post_thread_message(
        self,
        channel: str,
        thread_ts: str,
        *,
        text: str | None = None,
        blocks: list[Mapping[str, Any]] | None = None,
    ) -> SlackResponse:
        return await self.post_message(
            channel,
            text=text,
            blocks=blocks,
            thread_ts=thread_ts,
        )

    async def update_message(
        self,
        channel: str,
        ts: str,
        *,
        text: str | None = None,
        blocks: list[Mapping[str, Any]] | None = None,
    ) -> SlackResponse:
        payload: MutableMapping[str, Any] = {"channel": channel, "ts": ts}
        if text is not None:
            payload["text"] = text
        if blocks is not None:
            payload["blocks"] = blocks
        return await self._call("chat.update", payload)

    async def post_ephemeral(
        self,
        channel: str,
        user: str,
        *,
        text: str,
        blocks: list[Mapping[str, Any]] | None = None,
    ) -> SlackResponse:
        payload: MutableMapping[str, Any] = {"channel": channel, "user": user, "text": text}
        if blocks is not None:
            payload["blocks"] = blocks
        return await self._call("chat.postEphemeral", payload)

    async def open_view(
        self,
        trigger_id: str,
        view: Mapping[str, Any],
    ) -> SlackResponse:
        payload: MutableMapping[str, Any] = {"trigger_id": trigger_id, "view": view}
        return await self._call("views.open", payload)

    async def list_conversations(
        self,
        *,
        limit: int = 200,
        types: str = "public_channel,private_channel",
        cursor: str | None = None,
    ) -> SlackResponse:
        params: MutableMapping[str, Any] = {"limit": limit, "types": types}
        if cursor:
            params["cursor"] = cursor
        return await self._call("conversations.list", params, method="GET")

    async def _call(
        self,
        method_name: str,
        payload: Mapping[str, Any],
        *,
        method: str = "POST",
    ) -> SlackResponse:
        try:
            if method == "POST":
                response = await self._client.post(f"/{method_name}", json=payload)
            else:
                response = await self._client.get(f"/{method_name}", params=payload)

            response.raise_for_status()
            data = response.json()
            if not data.get("ok", False):
                raise SlackError(data.get("error", "unknown_error"))
            return SlackResponse(ok=True, data=data)
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            raise SlackError(str(exc)) from exc
