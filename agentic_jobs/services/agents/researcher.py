from __future__ import annotations

import json
import logging
from typing import Any

from agentic_jobs.services.agents.base import BaseAgent
from agentic_jobs.services.agents.schemas import ResearchBrief
from agentic_jobs.services.llm.prompt_builder import ProfileBundle
from agentic_jobs.services.llm.style_kit import CoverLetterKit
from agentic_jobs.services.research.scraper import ScrapedPage
from agentic_jobs.services.vault.retriever import VaultMatch


LOGGER = logging.getLogger(__name__)

# Context budget (chars) for each input section — keeps total user message
# under ~6000 chars which is comfortable for an 8K context window model.
_MAX_JD_CHARS = 2000
_MAX_COMPANY_CHARS = 2500
_MAX_VAULT_CHARS = 800   # per excerpt
_MAX_VAULT_EXCERPTS = 4
_MAX_MEMORY_CHARS = 1000


class ResearcherAgent(BaseAgent[ResearchBrief]):
    """
    Analyzes the job description, scraped company data, vault excerpts, and
    candidate profile to produce a ResearchBrief for the WriterAgent.

    This agent does NOT call external APIs or the web — all data gathering
    is done before this agent is called (by PipelineCoordinator).
    The agent's job is synthesis: connecting dots between company, role, and candidate.
    """

    agent_name = "researcher"
    temperature = 0.2

    def system_prompt(self, **kwargs: Any) -> str:
        return (
            "You are a job application research analyst. Your task is to analyze a job description, "
            "company information, and a candidate's background, then produce a structured research brief "
            "that a cover letter writer will use to craft a tailored, strategic cover letter.\n\n"
            "Focus on:\n"
            "- What does this company actually do and care about? What are their values/mission?\n"
            "- What are the 3-5 core themes this role requires (beyond surface-level skills)?\n"
            "- Which specific candidate experiences and projects directly map to those themes?\n"
            "- Which project from the candidate's portfolio is the strongest fit to highlight?\n"
            "- What hard requirements from the JD must the cover letter address?\n\n"
            "CRITICAL CONSTRAINT: matched_experiences must ONLY reference experiences from the "
            "candidate data provided below (experience_highlights and vault_excerpts). "
            "Do NOT infer, extrapolate, or invent experiences not explicitly listed. "
            "The vault_excerpts are PRIMARY SOURCES written by the candidate — treat them as "
            "authoritative facts. If the JD requires X and no candidate experience covers X, "
            "do not include it in matched_experiences. "
            "Fewer accurate matches beats many fabricated ones.\n\n"
            "SELECTION RULE: Return at most 2 matched_experiences — the two strongest, most specific "
            "matches only. Do not return every possible match. Quality over coverage. "
            "Also identify the single PRIMARY experience (the best fit) that should anchor the entire "
            "letter — the writer will build the narrative around this one.\n\n"
            "Respond ONLY with valid JSON matching this schema exactly:\n"
            "{\n"
            '  "company_context": "2-3 sentence summary of company mission/products/culture",\n'
            '  "role_themes": ["theme1", "theme2", "theme3"],\n'
            '  "jd_requirements": ["requirement1", "requirement2"],\n'
            '  "matched_experiences": ["best match only — 2 max"],\n'
            '  "primary_experience": "the single experience to anchor the letter around",\n'
            '  "suggested_project": "project name from the kit to highlight",\n'
            '  "memory_notes": ["any relevant memory notes to carry forward"]\n'
            "}\n"
            "Do not wrap the JSON in code fences. Do not add commentary outside the JSON."
        )

    def build_user_message(self, **kwargs: Any) -> str:
        jd_text: str = kwargs["jd_text"]
        company_name: str = kwargs["company_name"]
        scraped_pages: list[ScrapedPage] = kwargs.get("scraped_pages", [])
        vault_matches: list[VaultMatch] = kwargs.get("vault_matches", [])
        profile: ProfileBundle = kwargs["profile"]
        kit: CoverLetterKit = kwargs["kit"]
        memory_notes: list[str] = kwargs.get("memory_notes", [])

        # Compile company context from scraped pages
        company_text_parts = []
        for page in scraped_pages:
            if page.text:
                company_text_parts.append(f"[{page.title or page.url}]\n{page.text}")
        company_text = self._truncate("\n\n".join(company_text_parts), _MAX_COMPANY_CHARS)

        # Format vault excerpts
        vault_parts = []
        for match in vault_matches[:_MAX_VAULT_EXCERPTS]:
            excerpt = self._truncate(match.text, _MAX_VAULT_CHARS)
            vault_parts.append(f"[{match.heading} | {match.file_path} | score={match.score:.2f}]\n{excerpt}")
        vault_text = "\n\n".join(vault_parts) if vault_parts else "None available."

        # Experience highlights from kit — full bullets, not just title/summary
        experience_parts = []
        for exp in kit.experience:
            lines = [f"### {exp.title}", f"Summary: {exp.summary}", "Verified facts:"]
            for bullet in exp.bullets:
                lines.append(f"  - {bullet}")
            experience_parts.append("\n".join(lines))
        experience_text = "\n\n".join(experience_parts) if experience_parts else "None available."

        # Projects from kit
        project_names = [p.name for p in kit.projects]

        payload = {
            "company_name": company_name,
            "jd_text": self._truncate(jd_text, _MAX_JD_CHARS),
            "scraped_company_info": company_text or "No company data available.",
            "vault_excerpts": vault_text,
            "candidate": {
                "name": profile.full_name,
                "skills": profile.skills[:15],
                "stack": profile.stack[:10],
                "experience_highlights": experience_text,
                "projects_available": project_names,
            },
            "memory_notes": [self._truncate(n, 200) for n in memory_notes[:5]],
        }
        return json.dumps(payload, ensure_ascii=False)

    def parse_response(self, raw: dict[str, Any]) -> ResearchBrief:
        return ResearchBrief(
            company_name=raw.get("company_name", ""),
            company_domain="",  # filled in by coordinator
            company_context=raw.get("company_context", ""),
            role_themes=list(raw.get("role_themes", [])),
            jd_requirements=list(raw.get("jd_requirements", [])),
            matched_experiences=list(raw.get("matched_experiences", []))[:2],
            primary_experience=raw.get("primary_experience", ""),
            vault_excerpts=[],   # raw excerpts stored by coordinator
            memory_notes=list(raw.get("memory_notes", [])),
            suggested_project=raw.get("suggested_project", ""),
        )
