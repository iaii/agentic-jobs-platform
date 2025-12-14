from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
import json
import asyncio

import httpx

from agentic_jobs.config import settings


@dataclass(slots=True)
class LlmResponse:
    version: str
    cover_letter_md: str
    sections_used: list[str]
    provenance: dict[str, Any]


async def _mock_generate(payload: Mapping[str, Any]) -> LlmResponse:
    role = payload.get("role", {})
    project = payload.get("project_card", {})
    profile = payload.get("profile", {})
    name = (profile.get("identity") or {}).get("name") or "Apoorva"

    opener = f"Dear Hiring Manager,\n\nI am excited to apply for the {role.get('title')} role at {role.get('company')}."
    why_company = (
        f"Your focus on {role.get('title', '').lower()} matches what I build in {project.get('short_name')}."
    )
    fit = "I love shipping small, tested updates that include logging and docs so teammates can build with confidence."
    close = "Thank you for considering my application.\n\nSincerely,\n" + name
    body = "\n\n".join([opener, why_company, fit, close])
    return LlmResponse(
        version="CL v1",
        cover_letter_md=body,
        sections_used=["opener", "why_company", "role_alignment", "close"],
        provenance={"project": project.get("short_name")},
    )


async def generate_cover_letter(payload: Mapping[str, Any]) -> LlmResponse:
    backend = (settings.llm_backend or "mock").lower()
    if backend == "mock":
        return await _mock_generate(payload)
    if backend == "qwen":
        return await _call_qwen_backend(payload)
    if backend == "ollama":
        return await _call_openai_style_backend(payload)
    raise RuntimeError(f"LLM backend '{backend}' is not configured.")


async def summarize_feedback(notes: Sequence[str]) -> str:
    if not notes:
        return "No new notes captured."
    latest = notes[-1]
    return f"Latest preference noted: {latest.strip()[:280]}"


class LlmBackendError(RuntimeError):
    """Raised when the LLM backend fails."""


async def _call_qwen_backend(payload: Mapping[str, Any]) -> LlmResponse:
    if not settings.llm_endpoint_url:
        raise LlmBackendError("LLM_ENDPOINT_URL is not configured.")
    api_key = settings.llm_api_key or settings.ollama_api_key
    if not api_key:
        raise LlmBackendError("LLM_API_KEY is not configured.")

    system_prompt = (
        "You are a cover letter generator. Respond ONLY with JSON matching the schema "
        '{"version":"CL vN","cover_letter_md":"markdown","sections_used":["..."],"provenance":{...}}. '
        "Do not add backticks or commentary."
    )
    messages = [
        {
            "role": "system",
            "content": [{"type": "text", "text": system_prompt}],
        },
        {
            "role": "user",
            "content": [{"type": "text", "text": json.dumps(payload)}],
        },
    ]
    body = {
        "model": settings.llm_model_name,
        "input": {"messages": messages},
        "parameters": {
            "result_format": "json",
        },
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    timeout = max(5.0, float(settings.llm_timeout_seconds or 60))
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            settings.llm_endpoint_url,
            json=body,
            headers=headers,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LlmBackendError(f"Qwen backend error: {exc.response.text}") from exc
        data = response.json()
    try:
        message = data["output"]["choices"][0]["message"]
        content = message["content"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmBackendError("Unexpected Qwen response format.") from exc
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LlmBackendError("Qwen response was not valid JSON.") from exc

    try:
        version = parsed["version"]
        letter = parsed["cover_letter_md"]
        sections = parsed.get("sections_used", [])
        provenance = parsed.get("provenance", {})
    except KeyError as exc:
        raise LlmBackendError("Qwen JSON missing required fields.") from exc
    return LlmResponse(
        version=version,
        cover_letter_md=letter,
        sections_used=list(sections),
        provenance=provenance,
    )


async def _call_openai_style_backend(payload: Mapping[str, Any]) -> LlmResponse:
    if not settings.llm_endpoint_url:
        raise LlmBackendError("LLM_ENDPOINT_URL is not configured.")
    api_key = settings.llm_api_key or settings.ollama_api_key
    if not api_key:
        raise LlmBackendError("LLM_API_KEY is not configured.")
    system_prompt = (
        "You are a cover letter generator. Respond ONLY with JSON matching the schema "
        '{"version":"CL vN","cover_letter_md":"markdown","sections_used":["..."],"provenance":{...}}. '
        "Do not wrap the JSON in code fences."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(payload)},
    ]
    body = {
        "model": settings.llm_model_name,
        "messages": messages,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    timeout = max(5.0, float(settings.llm_timeout_seconds or 60))
    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(2):
            try:
                response = await client.post(
                    settings.llm_endpoint_url,
                    json=body,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
                break
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if attempt == 0 and exc.response.status_code in {429, 500, 502, 503, 504}:
                    await asyncio.sleep(2)
                    continue
                raise LlmBackendError(f"Ollama backend error: {exc.response.text}") from exc
            except httpx.RequestError as exc:
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(2)
                    continue
                raise LlmBackendError(f"Ollama backend request error: {exc}") from exc
        else:
            raise LlmBackendError(f"Ollama backend error: {last_error}") from last_error

    try:
        choice = data["choices"][0]
        content = choice["message"]["content"]
        if isinstance(content, list):
            text = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        else:
            text = content
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmBackendError("Unexpected Ollama/OpenAI response format.") from exc

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LlmBackendError("Ollama response was not valid JSON.") from exc

    try:
        version = parsed["version"]
        letter = parsed["cover_letter_md"]
        sections = parsed.get("sections_used", [])
        provenance = parsed.get("provenance", {})
    except KeyError as exc:
        raise LlmBackendError("Ollama JSON missing required fields.") from exc
    return LlmResponse(
        version=version,
        cover_letter_md=letter,
        sections_used=list(sections),
        provenance=provenance,
    )
