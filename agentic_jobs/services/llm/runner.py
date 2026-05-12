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
    name = (profile.get("identity") or {}).get("name") or settings.profile_fallback_name

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
    if backend in {"ollama", "lmstudio"}:
        return await _call_openai_style_backend(payload)
    raise RuntimeError(f"LLM backend '{backend}' is not configured.")


async def summarize_feedback(notes: Sequence[str]) -> str:
    if not notes:
        return "No new notes captured."
    latest = notes[-1]
    return f"Latest preference noted: {latest.strip()[:280]}"


class LlmBackendError(RuntimeError):
    """Raised when the LLM backend fails."""


# ---------------------------------------------------------------------------
# Generic agent LLM call — used by all three agents (Researcher, Writer, HM)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class AgentLlmResponse:
    content: dict[str, Any]
    raw_text: str


async def call_llm(
    system_prompt: str,
    user_message: str,
    *,
    temperature: float = 0.3,
) -> AgentLlmResponse:
    """
    Generic OpenAI-compatible LLM call for agent use.

    Unlike _call_openai_style_backend (which is hard-coded to the CL JSON schema),
    this accepts arbitrary system/user prompts and returns whatever JSON the model
    produces. Each agent defines and validates its own response shape.

    Reuses the same retry logic, code-fence stripping, and JSON parsing.
    Reads endpoint/model/key from settings (same as the cover letter pipeline).
    """
    if not settings.llm_endpoint_url:
        raise LlmBackendError("LLM_ENDPOINT_URL is not configured.")

    max_chars = settings.llm_max_user_msg_chars
    if len(user_message) > max_chars:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "call_llm: user message truncated from %d to %d chars to fit context window",
            len(user_message),
            max_chars,
        )
        user_message = user_message[:max_chars]

    api_key = settings.llm_api_key or "lm-studio"
    body: dict[str, Any] = {
        "model": settings.llm_model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": temperature,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    timeout = max(5.0, float(settings.llm_timeout_seconds or 120))
    last_error: Exception | None = None
    max_attempts = 3

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(max_attempts):
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
                retryable = exc.response.status_code in {429, 500, 502, 503, 504}
                if retryable and attempt < max_attempts - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise LlmBackendError(
                    f"LLM agent call HTTP error {exc.response.status_code}: {exc.response.text}"
                ) from exc
            except httpx.RequestError as exc:
                last_error = exc
                if attempt < max_attempts - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise LlmBackendError(f"LLM agent call request error: {exc}") from exc
        else:
            raise LlmBackendError(f"LLM agent call failed after {max_attempts} attempts: {last_error}") from last_error

    try:
        raw_text = data["choices"][0]["message"]["content"]
        if isinstance(raw_text, list):
            raw_text = "".join(p.get("text", "") for p in raw_text if isinstance(p, dict))
        else:
            raw_text = str(raw_text)
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmBackendError("Unexpected LLM response format in agent call.") from exc

    # Strip code fences if the model wrapped JSON anyway
    stripped = raw_text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        # raw_decode parses the first complete JSON object and ignores trailing text,
        # which handles models that continue writing after the closing brace.
        parsed, _ = json.JSONDecoder(strict=False).raw_decode(stripped)
        return AgentLlmResponse(content=parsed, raw_text=raw_text)
    except json.JSONDecodeError:
        pass

    # JSON parse failed — retry once with an explicit correction message.
    # Common cause: model wraps quoted text in string values using bare " characters
    # (e.g. feedback: [""As a seasoned engineer..."]) which breaks the JSON parser.
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "call_llm: JSON parse failed on first attempt, retrying with correction prompt"
    )
    body["messages"].append({"role": "assistant", "content": raw_text})
    body["messages"].append({
        "role": "user",
        "content": (
            "Your previous response contained invalid JSON — likely due to unescaped "
            "quotation marks inside string values. Rewrite your response as valid JSON. "
            "Do not use quotation marks inside string values; paraphrase any quoted text instead."
        ),
    })
    async with httpx.AsyncClient(timeout=timeout) as retry_client:
        try:
            retry_response = await retry_client.post(
                settings.llm_endpoint_url,
                json=body,
                headers=headers,
            )
            retry_response.raise_for_status()
            retry_data = retry_response.json()
            raw_text = str(retry_data["choices"][0]["message"]["content"])
            stripped = raw_text.strip()
            if stripped.startswith("```"):
                stripped = stripped.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            parsed, _ = json.JSONDecoder(strict=False).raw_decode(stripped)
            return AgentLlmResponse(content=parsed, raw_text=raw_text)
        except Exception as exc:
            raise LlmBackendError(f"Agent LLM response was not valid JSON: {stripped[:300]}") from exc


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
    """Shared backend for LM Studio, Ollama, and any OpenAI-compatible endpoint."""
    backend = (settings.llm_backend or "lmstudio").lower()
    if not settings.llm_endpoint_url:
        raise LlmBackendError("LLM_ENDPOINT_URL is not configured.")

    # LM Studio does not require an API key; use a placeholder if none is configured.
    api_key = settings.llm_api_key or settings.ollama_api_key or "lm-studio"

    system_prompt = (
        "You are a cover letter generator. Respond ONLY with JSON matching the schema "
        '{"version":"CL vN","cover_letter_md":"markdown","sections_used":["..."],"provenance":{...}}. '
        "Do not wrap the JSON in code fences."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(payload)},
    ]
    body: dict[str, Any] = {
        "model": settings.llm_model_name,
        "messages": messages,
        "temperature": 0.2,
    }
    # Older LM Studio accepted json_object; newer versions only accept json_schema or text.
    # We rely on the system prompt instruction + code-fence stripping instead.

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    timeout = max(5.0, float(settings.llm_timeout_seconds or 120))
    last_error: Exception | None = None
    max_attempts = 3
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(max_attempts):
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
                retryable = exc.response.status_code in {429, 500, 502, 503, 504}
                if retryable and attempt < max_attempts - 1:
                    wait = 2 ** attempt  # exponential back-off: 1s, 2s
                    await asyncio.sleep(wait)
                    continue
                raise LlmBackendError(
                    f"{backend} backend HTTP error {exc.response.status_code}: {exc.response.text}"
                ) from exc
            except httpx.RequestError as exc:
                last_error = exc
                if attempt < max_attempts - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise LlmBackendError(f"{backend} backend request error: {exc}") from exc
        else:
            raise LlmBackendError(f"{backend} backend failed after {max_attempts} attempts: {last_error}") from last_error

    try:
        choice = data["choices"][0]
        content = choice["message"]["content"]
        if isinstance(content, list):
            text = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        else:
            text = str(content)
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmBackendError(f"Unexpected {backend} response format.") from exc

    # Strip markdown code fences if the model wrapped the JSON anyway
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        parsed, _ = json.JSONDecoder(strict=False).raw_decode(stripped)
    except json.JSONDecodeError as exc:
        raise LlmBackendError(f"{backend} response was not valid JSON: {stripped[:200]}") from exc

    try:
        version = parsed["version"]
        letter = parsed["cover_letter_md"]
        sections = parsed.get("sections_used", [])
        provenance = parsed.get("provenance", {})
    except KeyError as exc:
        raise LlmBackendError(f"{backend} JSON missing required fields: {exc}") from exc
    return LlmResponse(
        version=version,
        cover_letter_md=letter,
        sections_used=list(sections),
        provenance=provenance,
    )
