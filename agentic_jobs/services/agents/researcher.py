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
            "You are a job application research analyst. Analyze a job description, company information, "
            "and a candidate's background. Produce a structured research brief for a cover letter writer.\n\n"
            "Focus on:\n"
            "- What does this company actually do and care about? Their real technical problems, not their tagline.\n"
            "- What are the 3-5 core themes this role requires (beyond surface-level skills)?\n"
            "- Which of the candidate's verified experiences best maps to those themes?\n"
            "- Which project from the candidate's portfolio is the strongest fit?\n"
            "- What hard requirements from the JD must the cover letter address?\n\n"
            "SELECTION RULE — experience keys:\n"
            "You will receive a list called 'valid_experience_keys'. You MUST select keys only from "
            "that list. Do NOT invent keys. Return at most 2 matched_experience_keys — the two "
            "strongest, most specific matches. Also pick a single primary_experience_key — the one "
            "experience that should anchor the entire letter. It may also appear in matched_experience_keys.\n"
            "If the JD requires X and no experience covers it, omit it. Fewer accurate picks beats "
            "many weak ones.\n\n"
            "Use domain_hints (if provided) to decide which kind of experience to emphasize based on "
            "the company's domain (search, productivity, health, hardware, etc.).\n\n"
            "Respond ONLY with valid JSON matching this schema exactly:\n"
            "{\n"
            '  "company_context": "2-3 sentence summary of company mission/products/culture",\n'
            '  "role_themes": ["theme1", "theme2", "theme3"],\n'
            '  "jd_requirements": ["requirement1", "requirement2"],\n'
            '  "primary_experience_key": "one key from valid_experience_keys",\n'
            '  "matched_experience_keys": ["key1", "key2"],\n'
            '  "suggested_project": "project key from valid_project_keys",\n'
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

        # Experience highlights from kit — full bullets with keys so researcher can pick
        experience_parts = []
        for exp in kit.experience:
            lines = [f"### {exp.title} [key: {exp.key}]", f"Summary: {exp.summary}", "Verified facts:"]
            for bullet in exp.bullets:
                lines.append(f"  - {bullet}")
            experience_parts.append("\n".join(lines))
        experience_text = "\n\n".join(experience_parts) if experience_parts else "None available."

        # Valid selection keys
        valid_experience_keys = [exp.key for exp in kit.experience]
        valid_project_keys = [p.key for p in kit.projects]

        payload = {
            "company_name": company_name,
            "jd_text": self._truncate(jd_text, _MAX_JD_CHARS),
            "scraped_company_info": company_text or "No company data available.",
            "vault_excerpts": vault_text,
            "candidate": {
                "name": profile.full_name,
                "bio": kit.profile.bio,
                "identity_notes": kit.profile.identity_notes,
                "what_cover_letter_should_add": kit.profile.what_cover_letter_should_add,
                "skills": profile.skills[:15],
                "stack": profile.stack[:10],
                "experience_highlights": experience_text,
            },
            "valid_experience_keys": valid_experience_keys,
            "valid_project_keys": valid_project_keys,
            "domain_hints": kit.domain_hints,
            "memory_notes": [self._truncate(n, 200) for n in memory_notes[:5]],
        }
        return json.dumps(payload, ensure_ascii=False)

    def parse_response(self, raw: dict[str, Any]) -> ResearchBrief:
        matched_keys = list(raw.get("matched_experience_keys", []))[:2]
        primary_key = raw.get("primary_experience_key", "")
        return ResearchBrief(
            company_name=raw.get("company_name", ""),
            company_domain="",          # filled by coordinator
            company_context=raw.get("company_context", ""),
            role_themes=list(raw.get("role_themes", [])),
            jd_requirements=list(raw.get("jd_requirements", [])),
            matched_experiences=[],     # filled by coordinator after key resolution
            primary_experience="",      # filled by coordinator after key resolution
            vault_excerpts=[],          # filled by coordinator
            memory_notes=list(raw.get("memory_notes", [])),
            suggested_project=raw.get("suggested_project", ""),
            primary_experience_key=primary_key,
            matched_experience_keys=matched_keys,
        )
