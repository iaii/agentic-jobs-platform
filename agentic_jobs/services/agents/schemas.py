from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentic_jobs.services.vault.retriever import VaultMatch


@dataclass(slots=True)
class ResearchBrief:
    """Output of the ResearcherAgent. Passed to both WriterAgent and HiringManagerAgent."""
    company_name: str
    company_domain: str
    company_context: str          # Scraped + synthesized summary of company mission/products
    role_themes: list[str]        # 3-5 key themes extracted from the JD
    jd_requirements: list[str]    # Parsed hard requirements from JD
    matched_experiences: list[str]  # Candidate experiences that map to JD themes (max 2)
    primary_experience: str        # The single best-fit experience to anchor the letter
    vault_excerpts: list[str]     # Relevant vault section text (already truncated)
    memory_notes: list[str]       # Long-term learnings from memory store
    suggested_project: str        # Which project from the kit to highlight


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
