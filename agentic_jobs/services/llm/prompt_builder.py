from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from agentic_jobs.db import models
from agentic_jobs.services.llm.style_kit import (
    CoverLetterKit,
    cover_letter_kit_hash,
)


VISUAL_KEYWORDS = {
    "fashion",
    "visual",
    "3d",
    "outfit",
    "design",
}

HEALTH_KEYWORDS = {
    "health",
    "wellness",
    "fitness",
    "nutrition",
    "behavior",
}

AUTOMATION_KEYWORDS = {
    "slack",
    "workflow",
    "productivity",
    "internal",
    "automation",
    "bot",
}

ROLE_TARGETS = [
    "backend",
    "APIs",
    "SQL",
    "React",
    "Python",
    "Next.js",
    "TypeScript",
    "testing",
    "metrics",
    "logging",
]

IMPACT_DEFAULTS = ["RAG evaluation", "Anomaly detection", "Exec reporting"]
PLAN_DEFAULTS = [
    "pair during onboarding",
    "ship a scoped backend fix with tests",
    "add observability for one flow",
]
STACK_DEFAULTS = ["Java", "Python", "SQL", "TypeScript/React", "REST APIs", "Docker"]


@dataclass(slots=True)
class FeedbackNote:
    role: str
    text: str


@dataclass(slots=True)
class ProfileBundle:
    full_name: str
    preferred_name: str | None
    email: str | None
    phone: str | None
    base_location: str | None
    links: dict[str, str]
    skills: list[str]
    stack: list[str]
    projects: list[dict[str, str]]


@dataclass(slots=True)
class DraftContext:
    application: models.Application
    job: models.Job
    profile: ProfileBundle
    notes: list[str]
    feedback_history: list[FeedbackNote]
    learning_notes: list[str]


def _summarize_sentences(text: str, max_sentences: int = 3) -> str:
    sentences = [segment.strip() for segment in re.split(r"(?<=[.!?])\s+", text) if segment.strip()]
    if not sentences:
        return text[:240]
    return " ".join(sentences[:max_sentences])


def _extract_bullets(job: models.Job, limit: int = 5) -> list[str]:
    bullets: list[str] = []
    for item in job.requirements or []:
        match item:
            case {"text": value} if isinstance(value, str):
                bullets.append(value.strip())
            case {"description": value} if isinstance(value, str):
                bullets.append(value.strip())
            case str(value):
                bullets.append(value.strip())
            case _:
                continue
    if not bullets:
        text = job.jd_text.split("\n")
        bullets = [line.strip() for line in text if line.strip()]
    return bullets[:limit]


def _extract_phrases(job: models.Job, limit: int = 5) -> list[str]:
    phrases: list[str] = []
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9\+\-/&]+", job.jd_text)
    seen: set[str] = set()
    for token in tokens:
        lower = token.lower()
        if lower in seen:
            continue
        seen.add(lower)
        if lower in {"the", "and", "with", "this", "that"}:
            continue
        phrases.append(token)
        if len(phrases) >= limit:
            break
    return phrases


def _pick_theme(job: models.Job) -> str:
    text = f"{job.title} {job.jd_text}".lower()
    if any(keyword in text for keyword in HEALTH_KEYWORDS):
        return "health"
    if any(keyword in text for keyword in AUTOMATION_KEYWORDS):
        return "automation"
    if any(keyword in text for keyword in VISUAL_KEYWORDS):
        return "visual"
    return "visual"


def _select_project(kit: CoverLetterKit, theme: str) -> dict[str, str]:
    project = kit.find_project_by_theme(theme) or kit.projects[0]
    return {
        "name": project.name,
        "short_name": project.short_name,
        "summary": project.summary,
        "talking_points": project.talking_points,
        "themes": project.themes,
    }


def _select_role_targets(job: models.Job) -> list[str]:
    lower_text = job.jd_text.lower()
    targets: list[str] = []
    for target in ROLE_TARGETS:
        if target.lower() in lower_text:
            targets.append(target)
    if not targets:
        targets = ["backend", "APIs", "testing"]
    return targets[:4]


def _compose_why_company(job: models.Job, project: dict[str, str]) -> list[str]:
    focus_points = [
        f"The mission at {job.company_name} aligns with building products people actually use.",
        f"The role touches on {job.title.lower()} challenges where {project['short_name']} experience fits.",
    ]
    return focus_points


def _compose_stack(profile: ProfileBundle) -> list[str]:
    if profile.stack:
        return profile.stack[:5]
    return STACK_DEFAULTS


def _structure_impact_samples(kit: CoverLetterKit) -> list[str]:
    samples = kit.structure.impact.samples
    return samples or IMPACT_DEFAULTS


def _structure_plan_samples(kit: CoverLetterKit) -> list[str]:
    bullets = kit.structure.plan.bullets
    return bullets or PLAN_DEFAULTS


def _skills_card_dict(kit: CoverLetterKit) -> dict[str, list[str]]:
    return kit.skills.as_dict()


def _experience_payload(kit: CoverLetterKit) -> list[dict[str, object]]:
    return [
        {
            "title": exp.title,
            "summary": exp.summary,
            "bullets": exp.bullets,
            "themes": exp.themes,
        }
        for exp in kit.experience
    ]


def _structure_payload(kit: CoverLetterKit) -> dict[str, object]:
    return {
        "greeting": kit.structure.greeting,
        "opener": kit.structure.opener_guidance,
        "impact_section": {
            "label": kit.structure.impact.label,
            "guidance": kit.structure.impact.description,
            "samples": kit.structure.impact.samples,
        },
        "plan_section": {
            "label": kit.structure.plan.label,
            "bullets": kit.structure.plan.bullets,
        },
        "stack_guidance": kit.structure.stack_guidance,
        "close_guidance": kit.structure.close_guidance,
        "signoff": kit.structure.signoff,
    }


def _build_feedback_context(notes: Sequence[str], history: Sequence[FeedbackNote]) -> dict:
    recent_history = [
        {"role": entry.role, "text": entry.text}
        for entry in history[-5:]
    ]
    return {
        "latest_notes": list(notes),
        "history": recent_history,
    }


def build_prompt_payload(context: DraftContext, kit: CoverLetterKit) -> dict:
    job = context.job
    project = _select_project(kit, _pick_theme(job))
    jd_summary = {
        "summary": _summarize_sentences(job.jd_text),
        "bullets": _extract_bullets(job),
        "phrases": _extract_phrases(job),
        "tone_sample": job.jd_text[:280],
    }

    style_card = {
        "tone": kit.tone.overall,
        "voice": kit.tone.voice,
        "rules": [
            "short sentences",
            "no em dashes",
            "no semicolons",
            "active voice",
            "mirror up to ten percent of JD phrasing",
        ],
        "dislikes": kit.tone.dislikes,
        "likes": kit.tone.likes,
        "tailoring_checklist": kit.tailoring_checklist,
        "dos": kit.dos,
        "donts": kit.donts,
    }

    payload = {
        "kit_version": cover_letter_kit_hash(),
        "app_id": context.application.human_id,
        "role": {
            "title": job.title,
            "company": job.company_name,
            "location": job.location,
        },
        "job_url": job.url,
        "jd": jd_summary,
        "profile": {
            "identity": {
                "name": context.profile.full_name,
                "preferred_name": context.profile.preferred_name,
                "email": context.profile.email,
                "phone": context.profile.phone,
                "base_location": context.profile.base_location,
            },
            "links": context.profile.links,
            "skills": context.profile.skills,
            "projects": context.profile.projects,
            "stack": _compose_stack(context.profile),
        },
        "project_card": project,
        "style_card": style_card,
        "slots": {
            "opener_hint": f"Tie interest to {job.company_name}'s work and mention {project['short_name']}.",
            "why_company": _compose_why_company(job, project),
            "role_alignment_targets": _select_role_targets(job),
            "impact_picks": _structure_impact_samples(kit),
            "plan_hints": _structure_plan_samples(kit),
            "stack_focus": _compose_stack(context.profile),
        },
        "feedback": _build_feedback_context(context.notes, context.feedback_history),
        "learning_notes": context.learning_notes,
        "toolkit": {
            "education": kit.education,
            "skills_card": _skills_card_dict(kit),
            "experience_highlights": _experience_payload(kit),
            "leadership_highlights": kit.leadership_highlights,
            "style_examples": kit.style_examples,
             "reasoning_guidance": kit.reasoning_guidance,
            "structure": _structure_payload(kit),
        },
    }
    return payload
