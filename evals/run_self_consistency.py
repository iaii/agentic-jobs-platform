"""Tier 1 eval: Hiring Manager self-consistency / noise-floor check.

Runs `HiringManagerAgent.run()` N times against a fixed (CoverLetterDraft,
ResearchBrief, jd_text) sample and reports the variance/std-dev of the
resulting scores. This establishes the "noise floor" for the 8B judge —
per design doc §6, Tier 1 — before any pipeline change is judged via
score deltas.

Usage:
    python -m evals.run_self_consistency [--n 3] [--application-id <uuid>]

If --application-id is given, the script attempts to load the most recent
cover letter artifact + a minimal ResearchBrief for that application from
the DB. If that fails (no DB, no artifact, or no application id given), it
falls back to a hardcoded synthetic example.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import uuid

from agentic_jobs.services.agents.reviewer import HiringManagerAgent
from agentic_jobs.services.agents.schemas import CoverLetterDraft, ResearchBrief
from agentic_jobs.services.llm.style_kit import load_cover_letter_kit


SYNTHETIC_JD_TEXT = """
We are looking for a Backend Software Engineer to join our platform team.

Requirements:
- 2+ years of experience building production backend services (Python or similar)
- Experience with relational databases and API design
- Familiarity with cloud infrastructure (AWS/GCP)
- Strong communication skills and ability to work cross-functionally
"""

SYNTHETIC_DRAFT = CoverLetterDraft(
    version=1,
    content_md=(
        "Dear Hiring Manager,\n\n"
        "I'm excited to apply for the Backend Software Engineer role at Acme Corp. "
        "In my recent work, I designed and shipped a retrieval pipeline backed by a "
        "PostgreSQL database, exposing a REST API consumed by internal services. "
        "I reduced p95 query latency from 420ms to 180ms by introducing a caching "
        "layer and optimizing index usage, and deployed the service on AWS with "
        "automated CI/CD.\n\n"
        "I'd welcome the chance to bring this systems-oriented experience to your "
        "platform team.\n\n"
        "Sincerely,\nCandidate"
    ),
    word_count=90,
    sections_used=["experience", "closing"],
)

SYNTHETIC_RESEARCH_BRIEF = ResearchBrief(
    company_name="Acme Corp",
    company_domain="acme.example",
    company_context="Acme Corp builds developer tooling for distributed teams.",
    role_themes=["backend systems", "API design", "cloud infrastructure"],
    jd_requirements=[
        "2+ years of production backend experience (Python or similar)",
        "Relational databases and API design",
        "Cloud infrastructure (AWS/GCP)",
        "Strong communication / cross-functional collaboration",
    ],
    matched_experiences=["Retrieval Pipeline: Built REST API over PostgreSQL; reduced p95 latency to 180ms"],
    primary_experience="Retrieval Pipeline",
    vault_excerpts=[],
    memory_notes=[],
    suggested_project="Retrieval Pipeline",
)


def _load_sample(application_id: str | None) -> tuple[CoverLetterDraft, ResearchBrief, str, str, str]:
    """Return (draft, research_brief, jd_text, role_title, company_name) for the eval.

    Falls back to the hardcoded synthetic example if `application_id` is not
    given, or if loading from the DB fails for any reason.
    """
    if not application_id:
        return SYNTHETIC_DRAFT, SYNTHETIC_RESEARCH_BRIEF, SYNTHETIC_JD_TEXT, "Backend Software Engineer", "Acme Corp"

    try:
        from sqlalchemy import select

        from agentic_jobs.core.enums import ArtifactType
        from agentic_jobs.db import models
        from agentic_jobs.db.session import SessionLocal
        from agentic_jobs.services.artifacts.utils import load_artifact_text

        app_uuid = uuid.UUID(application_id)
        with SessionLocal() as session:
            application = session.get(models.Application, app_uuid)
            if application is None or application.job is None:
                raise ValueError(f"No application/job found for {application_id}")

            content = load_artifact_text(session, app_uuid, ArtifactType.COVER_LETTER_VERSION)
            if not content:
                raise ValueError(f"No cover letter artifact found for {application_id}")

            draft = CoverLetterDraft(version=1, content_md=content, word_count=len(content.split()))
            job = application.job
            brief = ResearchBrief(
                company_name=job.company_name,
                company_domain=job.company_website or "",
                company_context="",
                role_themes=[],
                jd_requirements=[],
                matched_experiences=[],
                primary_experience="",
                vault_excerpts=[],
                memory_notes=[],
                suggested_project="",
            )
            return draft, brief, job.jd_text, job.title, job.company_name
    except Exception as exc:  # noqa: BLE001 - eval script, fall back loudly but gracefully
        print(f"[run_self_consistency] Falling back to synthetic sample (reason: {exc})", file=sys.stderr)
        return SYNTHETIC_DRAFT, SYNTHETIC_RESEARCH_BRIEF, SYNTHETIC_JD_TEXT, "Backend Software Engineer", "Acme Corp"


async def main_async(n: int, application_id: str | None) -> None:
    draft, brief, jd_text, role_title, company_name = _load_sample(application_id)
    kit = load_cover_letter_kit()
    reviewer = HiringManagerAgent()

    scores: list[float] = []
    verdicts: list[str] = []

    for i in range(n):
        verdict = await reviewer.run(
            draft=draft,
            research_brief=brief,
            jd_text=jd_text,
            kit=kit,
            role_title=role_title,
            company_name=company_name,
            round_number=1,
            pass_threshold=7.0,
        )
        scores.append(verdict.score)
        verdicts.append(verdict.verdict)
        print(f"  run {i + 1}/{n}: score={verdict.score:.2f} verdict={verdict.verdict}")

    mean = statistics.mean(scores)
    stdev = statistics.stdev(scores) if len(scores) > 1 else 0.0

    print()
    print(f"N runs:    {n}")
    print(f"Scores:    {[round(s, 2) for s in scores]}")
    print(f"Mean:      {mean:.3f}")
    print(f"Std dev:   {stdev:.3f}")
    print(f"Min/Max:   {min(scores):.2f} / {max(scores):.2f}")
    print(f"Verdicts:  {verdicts}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Tier 1: HM self-consistency / noise-floor check")
    parser.add_argument("--n", type=int, default=3, help="Number of repeated HM runs (default: 3)")
    parser.add_argument(
        "--application-id",
        type=str,
        default=None,
        help="Application UUID to load a real draft+JD from the DB; falls back to a synthetic sample if omitted/unavailable",
    )
    args = parser.parse_args()
    asyncio.run(main_async(args.n, args.application_id))


if __name__ == "__main__":
    main()
