from __future__ import annotations

from typing import Any, TypedDict
from uuid import UUID

from agentic_jobs.services.agents.schemas import (
    CoverLetterDraft,
    ResearchBrief,
    ReviewVerdict,
)
from agentic_jobs.services.llm.prompt_builder import ProfileBundle
from agentic_jobs.services.llm.style_kit import CoverLetterKit


class PipelineState(TypedDict, total=False):
    """Shared state threaded through the LangGraph pipeline.

    Every key here corresponds to a local variable that previously lived in
    `PipelineCoordinator.run()`. Nodes read the slices they need and return
    partial updates (dicts) that LangGraph merges into this state.
    """

    # ---- identifiers / static inputs ----
    application_id: UUID
    job_id: UUID
    company_name: str
    company_domain: str | None
    jd_text: str
    role_title: str

    # ---- profile / kit / config ----
    profile: ProfileBundle
    kit: CoverLetterKit
    word_budget: int
    pass_threshold: float
    max_revisions: int

    # ---- user-supplied context ----
    notes: list[str]
    clean_notes: list[str]
    author: str | None
    post_to_slack: bool

    # ---- Phase 1: data gathering ----
    scraped_pages: list[Any]
    vault_matches: list[Any]
    memory_notes: list[str]

    # ---- Phase 2: research ----
    research_brief: ResearchBrief
    ordered_keys: list[str]

    # ---- Phase 3: write/review loop ----
    draft: CoverLetterDraft | None
    review_history: list[ReviewVerdict]
    revision_round: int

    # ---- bookkeeping ----
    agent_log: list[dict]
    started_at: float
