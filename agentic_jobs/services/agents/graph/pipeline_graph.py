from __future__ import annotations

import logging
import time

from langgraph.graph import END, StateGraph

from agentic_jobs.services.agents.graph.state import PipelineState
from agentic_jobs.services.agents.reviewer import HiringManagerAgent
from agentic_jobs.services.agents.researcher import ResearcherAgent
from agentic_jobs.services.agents.schemas import CoverLetterDraft, ReviewVerdict
from agentic_jobs.services.agents.writer import WriterAgent

LOGGER = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Nodes
#
# Persistence (Phase 4) and the Slack notification (Phase 5) remain in
# PipelineCoordinator.run() as a final step after `graph.ainvoke(...)`,
# rather than graph nodes — they need the SQLAlchemy session and Slack
# client, which are coordinator-owned resources, not pipeline state.
# ----------------------------------------------------------------------


async def gather_data_node(state: PipelineState) -> dict:
    """Phase 1: company scrape + vault search + memory notes.

    The actual I/O (scraping, vault search, memory load) is performed by
    the coordinator before invoking the graph and passed in via initial
    state, since those helpers depend on coordinator-owned resources
    (DB session, scraper instance). This node only records the agent_log
    entry for that phase, keeping the phase visible in the graph.
    """
    agent_log = list(state.get("agent_log", []))
    agent_log.append({
        "phase": "data_gathering",
        "scraped_pages": len(state.get("scraped_pages", [])),
        "vault_matches": len(state.get("vault_matches", [])),
        "memory_notes": len(state.get("memory_notes", [])),
    })
    return {"agent_log": agent_log}


async def research_node(state: PipelineState) -> dict:
    """Phase 2: ResearcherAgent + coordinator's experience-key validation.

    Preserves the invariant that experience keys are validated/resolved
    against the kit *here*, before the writer node ever sees them.
    """
    researcher = ResearcherAgent()
    kit = state["kit"]
    t0 = time.monotonic()

    research_brief = await researcher.run(
        jd_text=state["jd_text"],
        company_name=state["company_name"],
        scraped_pages=state.get("scraped_pages", []),
        vault_matches=state.get("vault_matches", []),
        profile=state["profile"],
        kit=kit,
        memory_notes=state.get("memory_notes", []),
    )

    research_brief.company_domain = state.get("company_domain") or ""
    research_brief.company_name = research_brief.company_name or state["company_name"]
    research_brief.vault_excerpts = [m.text for m in state.get("vault_matches", [])[:4]]

    # Resolve researcher's experience keys to verified bullets. The researcher
    # returns keys (e.g. "jci_rag_eval"); look up the actual ExperienceHighlight
    # from the kit so the writer never sees free-form LLM text.
    valid_keys = {e.key for e in kit.experience}
    primary_key = research_brief.primary_experience_key
    if primary_key and primary_key not in valid_keys:
        LOGGER.warning(
            "[graph.research] Researcher returned unknown primary key %r; falling back to first experience",
            primary_key,
        )
        primary_key = kit.experience[0].key if kit.experience else ""
        research_brief.primary_experience_key = primary_key

    matched_keys = [k for k in research_brief.matched_experience_keys if k in valid_keys]
    research_brief.matched_experience_keys = matched_keys

    ordered_keys: list[str] = []
    for k in ([primary_key] if primary_key else []) + matched_keys:
        if k not in ordered_keys:
            ordered_keys.append(k)

    primary_exp = next((e for e in kit.experience if e.key == primary_key), None)
    research_brief.primary_experience = primary_exp.title if primary_exp else ""

    matched_exps = [e for e in kit.experience if e.key in matched_keys]
    research_brief.matched_experiences = [
        f"{e.title}: {'; '.join(e.bullets[:2])}" for e in matched_exps
    ]

    agent_log = list(state.get("agent_log", []))
    agent_log.append({
        "phase": "researcher",
        "duration_ms": int((time.monotonic() - t0) * 1000),
        "themes": research_brief.role_themes,
        "suggested_project": research_brief.suggested_project,
    })

    return {
        "research_brief": research_brief,
        "ordered_keys": ordered_keys,
        "agent_log": agent_log,
    }


async def write_node(state: PipelineState) -> dict:
    """Phase 3a: WriterAgent produces (or revises) a draft."""
    writer = WriterAgent()
    revision_round = state.get("revision_round", 0)
    is_revision = revision_round > 0
    review_history: list[ReviewVerdict] = state.get("review_history", [])
    previous_draft: CoverLetterDraft | None = state.get("draft")

    t0 = time.monotonic()
    draft = await writer.run(
        research_brief=state["research_brief"],
        profile=state["profile"],
        kit=state["kit"],
        full_name=state["profile"].full_name,
        word_budget=state["word_budget"],
        matched_experience_keys=state.get("ordered_keys", []),
        is_revision=is_revision,
        previous_draft=previous_draft if is_revision else None,
        reviewer_feedback=review_history[-1] if is_revision else None,
        user_notes=state.get("clean_notes", []),
    )
    draft.version = revision_round + 1

    agent_log = list(state.get("agent_log", []))
    agent_log.append({
        "phase": f"writer_round_{draft.version}",
        "duration_ms": int((time.monotonic() - t0) * 1000),
        "word_count": draft.word_count,
    })

    return {"draft": draft, "agent_log": agent_log}


async def review_node(state: PipelineState) -> dict:
    """Phase 3b: HiringManagerAgent reviews the latest draft."""
    reviewer = HiringManagerAgent()
    draft = state["draft"]
    assert draft is not None
    revision_round = state.get("revision_round", 0)

    t0 = time.monotonic()
    verdict = await reviewer.run(
        draft=draft,
        research_brief=state["research_brief"],
        jd_text=state["jd_text"],
        kit=state["kit"],
        role_title=state["role_title"],
        company_name=state["company_name"],
        round_number=revision_round + 1,
        pass_threshold=state["pass_threshold"],
    )

    review_history = list(state.get("review_history", []))
    review_history.append(verdict)

    agent_log = list(state.get("agent_log", []))
    agent_log.append({
        "phase": f"reviewer_round_{draft.version}",
        "duration_ms": int((time.monotonic() - t0) * 1000),
        "score": verdict.score,
        "verdict": verdict.verdict,
    })

    return {
        "review_history": review_history,
        "agent_log": agent_log,
        "revision_round": revision_round + 1,
    }


async def finalize_node(state: PipelineState) -> dict:
    """Terminal node — no-op pass-through. Persistence/Slack happen in the coordinator."""
    return {}


# ----------------------------------------------------------------------
# Conditional edge: same break condition as the original `for` loop in
# coordinator.py — "always do at least 2 revisions (3 total writer calls);
# rounds 0 and 1 always revise regardless of score."
# ----------------------------------------------------------------------

def _should_continue(state: PipelineState) -> str:
    review_history = state.get("review_history", [])
    revision_round = state.get("revision_round", 0)  # already incremented by review_node
    max_revisions = state["max_revisions"]
    pass_threshold = state["pass_threshold"]

    verdict = review_history[-1]

    # revision_round here is "number of rounds completed so far" (1-indexed
    # after review_node ran). The original loop's `revision_round > 1` check
    # (0-indexed loop variable) corresponds to "more than 2 rounds completed".
    if revision_round > 2 and (verdict.verdict == "pass" or verdict.score >= pass_threshold):
        LOGGER.info("[graph] Draft passed review: score=%.1f", verdict.score)
        return "finalize"

    if revision_round - 1 < max_revisions:
        return "write"

    LOGGER.info("[graph] Max revisions reached. Accepting best draft (score=%.1f)", verdict.score)
    return "finalize"


def build_pipeline_graph():
    """Build and compile the cover-letter generation pipeline graph."""
    graph = StateGraph(PipelineState)

    graph.add_node("gather_data", gather_data_node)
    graph.add_node("research", research_node)
    graph.add_node("write", write_node)
    graph.add_node("review", review_node)
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("gather_data")
    graph.add_edge("gather_data", "research")
    graph.add_edge("research", "write")
    graph.add_edge("write", "review")
    graph.add_conditional_edges(
        "review",
        _should_continue,
        {"write": "write", "finalize": "finalize"},
    )
    graph.add_edge("finalize", END)

    return graph.compile()


# Module-level compiled graph, built once and reused across pipeline runs.
PIPELINE_GRAPH = build_pipeline_graph()
