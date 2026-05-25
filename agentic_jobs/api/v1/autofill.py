from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from typing import Any

from agentic_jobs.config import settings
from agentic_jobs.core.enums import ArtifactType, AutofillMode, AutofillTaskStatus
from agentic_jobs.db import models
from agentic_jobs.db.session import SessionLocal
from agentic_jobs.services.agents.guardrails import sanitize
from agentic_jobs.services.artifacts.utils import get_artifact_path, load_artifact_text
from agentic_jobs.services.autofill.geo import relocation_answer
from agentic_jobs.services.autofill.profile import ProfileLoadError, ProfileLoader
from agentic_jobs.services.autofill.status import process_status_update
from agentic_jobs.services.autofill.types import AutofillStatusUpdate
from agentic_jobs.services.llm.runner import LlmBackendError, call_llm
from agentic_jobs.services.slack.client import SlackClient


router = APIRouter(prefix="/autofill", tags=["autofill"])
LOGGER = logging.getLogger(__name__)


def get_session() -> Session:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def verify_autofill_token(
    x_autofill_token: str | None = Header(default=None, alias="X-Autofill-Token"),
) -> None:
    if not settings.autofill_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Autofill disabled")
    # If no token is configured, deny all requests — an unconfigured token must not be treated as "open"
    if not settings.autofill_api_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Autofill token not configured")
    if x_autofill_token != settings.autofill_api_token:
        LOGGER.warning(
            "Autofill token mismatch — received: %r (len=%d), configured: %r (len=%d)",
            x_autofill_token,
            len(x_autofill_token or ""),
            settings.autofill_api_token,
            len(settings.autofill_api_token or ""),
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid autofill token")


class AutofillPayloadResponse(BaseModel):
    application_id: str
    human_id: str
    mode: AutofillMode
    status: AutofillTaskStatus
    summary: dict[str, Any] = Field(default_factory=dict)


class AutofillStatusRequest(BaseModel):
    human_id: str
    status: AutofillTaskStatus
    message: str | None = None
    final_url: str | None = None
    blocked_reason: str | None = None
    screenshot_path: str | None = None
    metadata: dict[str, Any] | None = None


@router.get("/payload/{human_id}", response_model=AutofillPayloadResponse)
async def get_autofill_payload(
    human_id: str,
    _: None = Depends(verify_autofill_token),
    session: Session = Depends(get_session),
) -> AutofillPayloadResponse:
    application = _get_application(session, human_id)
    if not application:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")

    summary_path = get_artifact_path(session, application.id, ArtifactType.AUTOFILL_SUMMARY)
    if not summary_path or not summary_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Autofill summary not found")
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Malformed summary") from exc

    # Backfill cover_letter_text for summaries written before that field was added.
    # This ensures the extension can paste the finalized CL into textarea fields
    # even when the summary JSON pre-dates the cover_letter_text feature.
    if not summary.get("cover_letter_text"):
        cl_text = load_artifact_text(session, application.id, ArtifactType.COVER_LETTER_VERSION)
        if cl_text:
            summary["cover_letter_text"] = cl_text

    task = (
        session.execute(
            select(models.AutofillTask)
            .where(models.AutofillTask.application_id == application.id)
            .order_by(models.AutofillTask.created_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    mode = task.mode if task else AutofillMode.AUTOFILL
    status_value = task.status if task else AutofillTaskStatus.QUEUED
    return AutofillPayloadResponse(
        application_id=str(application.id),
        human_id=application.human_id,
        mode=mode,
        status=status_value,
        summary=summary,
    )


@router.post("/status", status_code=status.HTTP_202_ACCEPTED)
async def post_autofill_status(
    payload: AutofillStatusRequest,
    _: None = Depends(verify_autofill_token),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    application = _get_application(session, payload.human_id)
    if not application:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")

    update = AutofillStatusUpdate(
        human_id=payload.human_id,
        status=payload.status,
        message=payload.message,
        final_url=payload.final_url,
        blocked_reason=payload.blocked_reason,
        screenshot_path=payload.screenshot_path,
        metadata=payload.metadata,
    )

    slack_client: SlackClient | None = None
    if settings.slack_bot_token:
        slack_client = SlackClient(settings.slack_bot_token)
    try:
        await process_status_update(session, application, update, slack_client)
    finally:
        if slack_client:
            await slack_client.aclose()

    return {"message": "status accepted"}


class FieldDescriptor(BaseModel):
    selector: str
    label: str
    field_type: str  # "text", "textarea", "select", "radio", "checkbox"
    options: list[str] = Field(default_factory=list)


class AutofillAnswerRequest(BaseModel):
    human_id: str
    fields: list[FieldDescriptor]
    job_context: dict[str, Any] | None = None  # title, company, location from the summary


class AutofillAnswerResponse(BaseModel):
    answers: dict[str, str]
    skipped: list[str]


_ANSWER_SYSTEM_PROMPT = """\
You are a job application form-filling assistant. Fill in job application form fields using ONLY the provided candidate profile data.

Each field has an "id" (like "f0", "f1", ...), a "label", a "field_type", and optional "options".

Return a single flat JSON object where:
- keys are the field "id" values (e.g. "f0", "f3", "f12")
- values are answer strings
Do NOT include markdown, explanations, or code fences. Return ONLY the JSON object.

Example response: {"f0": "Jane", "f1": "jane@email.com", "f4": "Yes"}

Rules:
1. OMIT any field you cannot answer confidently from the profile. Never invent data.
2. For select/radio fields, the value must match one of the provided options exactly (case-insensitive). If no option matches, omit.
3. For checkbox fields labeled "I agree" or work-model acknowledgments, answer "Yes".
4. For gender/race/veteran/disability fields, use the compliance section. Pick the closest matching option.
5. For relocation, use quick_answers.willing_to_relocate exactly.
6. For sponsorship, use quick_answers.sponsorship_required exactly.
7. For work authorization, use quick_answers.us_authorized exactly.\
"""


@router.post("/answer", response_model=AutofillAnswerResponse)
async def post_autofill_answer(
    payload: AutofillAnswerRequest,
    _: None = Depends(verify_autofill_token),
    session: Session = Depends(get_session),
) -> AutofillAnswerResponse:
    loader = ProfileLoader()
    try:
        profile = loader.load(session)
        LOGGER.info("[autofill] profile loaded: name=%s email=%s compliance=%s quick_answers=%s",
                    profile.identity.full_name, profile.identity.email,
                    list(profile.compliance.keys()), list(profile.quick_answers.keys()))
    except ProfileLoadError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    # Sanitize field labels and cap at 60 chars before embedding in the LLM prompt.
    # The extension enriches labels with question context (up to 120 chars) for its
    # own display, but the LLM only needs enough context to identify the field — not
    # the full question sentence. Capping here keeps the prompt well under the model's
    # context window on any ATS form.
    # Replace opaque CSS selectors (e.g. [data-ajp-id='ajp-23']) with simple
    # numeric IDs (f0, f1, ...) so local 8B models reliably use them as JSON
    # keys. We maintain a reverse map to translate the LLM's answers back to
    # real selectors before returning to the extension.
    id_to_selector: dict[str, str] = {}
    sanitized_fields = []
    skipped_unlabelled = []
    for i, f in enumerate(payload.fields):
        if not f.label.strip():
            skipped_unlabelled.append(f.selector)
            continue
        field_id = f"f{i}"
        id_to_selector[field_id] = f.selector
        sanitized_fields.append({
            "id": field_id,
            "label": sanitize(f.label, source="form:label")[:60],
            "field_type": f.field_type,
            "options": f.options,
        })

    # Detect whether any field is asking for work-experience descriptions.
    # When yes: load the resume text verbatim so the LLM can answer word-for-word.
    # When no: skip facts entirely — identity/links/compliance/quick_answers is
    # sufficient and keeps the prompt small enough for the 8B local model.
    _EXPERIENCE_KEYWORDS = {
        "describe", "responsibilities", "duties", "accomplishments",
        "what did you do", "employment", "work history", "previous company",
        "current company", "job description", "tell us about your role",
    }
    needs_experience = any(
        any(kw in (f["label"] or "").lower() for kw in _EXPERIENCE_KEYWORDS)
        for f in sanitized_fields
    )

    resume_text: str | None = None
    if needs_experience:
        resume_text_path_str = (profile.files.resume_text_path
                                if hasattr(profile.files, "resume_text_path") else None)
        if resume_text_path_str:
            _rp = Path(str(resume_text_path_str))
            if _rp.exists():
                resume_text = _rp.read_text(encoding="utf-8")

    # Deterministically resolve whether relocation is needed based on metro-area
    # comparison, then inject the result so the LLM reads a fact, not a question.
    job_location = (payload.job_context or {}).get("location", "")
    willing_to_relocate = relocation_answer(profile.identity.base_location, job_location)
    quick_answers = {**profile.quick_answers, "willing_to_relocate": willing_to_relocate}

    profile_data: dict[str, Any] = {
        "identity": {
            "full_name": profile.identity.full_name,
            "preferred_name": profile.identity.preferred_name,
            "email": profile.identity.email,
            "phone": profile.identity.phone,
            "base_location": profile.identity.base_location,
            "address": {
                "line1": profile.identity.address.line1 if profile.identity.address else None,
                "city": profile.identity.address.city if profile.identity.address else None,
                "state": profile.identity.address.state if profile.identity.address else None,
                "postal_code": profile.identity.address.postal_code if profile.identity.address else None,
                "country": profile.identity.address.country if profile.identity.address else None,
            },
        },
        "links": profile.links,
        "compliance": profile.compliance,
        "quick_answers": quick_answers,
    }

    # Only include resume when the form actually asks for experience descriptions.
    # Omitting it from basic forms (name/email/EEO) keeps the prompt small enough
    # for local 8B models and avoids context-window overflow.
    if resume_text:
        profile_data["resume"] = resume_text

    user_message = json.dumps(
        {
            "profile": profile_data,
            "job": payload.job_context or {},
            "fields": sanitized_fields,
        },
        ensure_ascii=False,
    )

    try:
        LOGGER.info("[autofill] calling LLM with %d fields, message length %d chars",
                    len(sanitized_fields), len(user_message))
        llm_resp = await call_llm(_ANSWER_SYSTEM_PROMPT, user_message, temperature=0.0)
        raw_answers = llm_resp.content
        LOGGER.info("[autofill] LLM raw_answers type=%s keys=%s",
                    type(raw_answers).__name__, list(raw_answers.keys()) if isinstance(raw_answers, dict) else repr(raw_answers)[:200])
    except LlmBackendError as exc:
        LOGGER.warning("[autofill] LLM call failed: %s", exc)
        return AutofillAnswerResponse(
            answers={},
            skipped=[f.selector for f in payload.fields],
        )

    # Map LLM's field IDs (f0, f1, ...) back to real selectors
    answers: dict[str, str] = {}
    skipped: list[str] = list(skipped_unlabelled)

    for field_id, value in raw_answers.items():
        selector = id_to_selector.get(field_id)
        if selector is None:
            continue
        if not isinstance(value, str) or not value.strip():
            skipped.append(selector)
            continue
        answers[selector] = value

    # Any labelled field not answered or explicitly skipped goes to skipped
    answered = set(answers)
    for field_id, selector in id_to_selector.items():
        if selector not in answered and selector not in skipped:
            skipped.append(selector)

    return AutofillAnswerResponse(answers=answers, skipped=skipped)


def _get_application(session: Session, human_id: str) -> models.Application | None:
    return (
        session.execute(
            select(models.Application).where(models.Application.human_id == human_id)
        )
        .scalars()
        .first()
    )
