from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING  # noqa: F401

if TYPE_CHECKING:
    from agentic_jobs.services.vault.retriever import VaultMatch


@dataclass(slots=True)
class CompanyIntelligence:
    """
    Company stage and context signals extracted from the JD and scraped pages.
    Written to Obsidian notes for the candidate's reference — never passed to
    the WriterAgent or included in the cover letter.
    """
    stage_signals: list[str]   # e.g. ["mentions Series B", "RSUs suggest late-stage"]
    employee_scale: str        # e.g. "50-200 engineers", "10,000+ globally", ""
    equity_type: str           # "options", "RSUs", "unclear"
    notable_facts: list[str]   # founding year, notable customers, recent launches, etc.


@dataclass(slots=True)
class ResearchBrief:
    """Output of the ResearcherAgent. Passed to both WriterAgent and HiringManagerAgent."""
    company_name: str
    company_domain: str
    company_context: str          # Scraped + synthesized summary of company mission/products
    role_themes: list[str]        # 3-5 key themes extracted from the JD
    jd_requirements: list[str]    # Parsed hard requirements from JD
    matched_experiences: list[str]  # Formatted summaries of matched experiences (filled by coordinator)
    primary_experience: str        # Title of the primary experience (filled by coordinator)
    vault_excerpts: list[str]     # Relevant vault section text (filled by coordinator)
    memory_notes: list[str]       # Long-term learnings from memory store
    suggested_project: str        # Which project from the kit to highlight
    # Experience keys returned by ResearcherAgent — coordinator resolves these to verified bullets.
    # Defaults let the old constructor call in parse_response work unchanged.
    primary_experience_key: str = ""
    matched_experience_keys: list[str] = field(default_factory=list)
    # Company intelligence — written to Obsidian notes, not passed to WriterAgent.
    company_intelligence: CompanyIntelligence | None = None


@dataclass(slots=True)
class CoverLetterDraft:
    """Output of the WriterAgent."""
    version: int
    content_md: str
    word_count: int
    sections_used: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ReviewVerdict:
    """Output of the HiringManagerAgent."""
    score: float                          # 0-10 weighted rubric total
    verdict: str                          # "pass" or "revise"
    overall_impression: str               # One-sentence summary
    feedback: list[str]                   # Actionable revision items
    strengths: list[str]                  # What's working well
    areas_for_improvement: list[str]      # What to fix (mirrors feedback but structured)


@dataclass(slots=True)
class PipelineResult:
    """Returned by PipelineCoordinator.run(). Maps cleanly to DraftResult."""
    final_draft: CoverLetterDraft
    research_brief: ResearchBrief
    review_history: list[ReviewVerdict]
    pipeline_run_id: uuid.UUID
    total_duration_ms: int
