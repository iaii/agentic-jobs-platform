from __future__ import annotations

import json
import logging
from typing import Any

from agentic_jobs.services.agents.base import BaseAgent
from agentic_jobs.services.agents.constants import BANNED_PHRASES
from agentic_jobs.services.agents.schemas import CoverLetterDraft, ResearchBrief, ReviewVerdict
from agentic_jobs.services.documents.style import get_document_style
from agentic_jobs.services.llm.prompt_builder import ProfileBundle
from agentic_jobs.services.llm.style_kit import CoverLetterKit


LOGGER = logging.getLogger(__name__)

# Prompt improver integration point (future):
# Before calling WriterAgent.run(), the user notes passed in via `user_notes`
# can optionally be processed through an improve_user_prompt() function.
# This would rewrite vague notes like "make it more specific" into concrete
# instructions like "reference Stripe's payment infrastructure in the opener."
# Integration in PipelineCoordinator.run():
#   raw_notes = context.notes
#   # Future: improved_notes = await improve_user_prompt(raw_notes)
#   brief = await writer.run(..., user_notes=improved_notes)
# The original notes are always persisted to DB unchanged.


def compute_word_budget() -> int:
    """
    Compute the maximum word count that fits on one page in the current DOCX style.

    Uses DocumentStyle's geometric properties (page size, margins, font, line spacing)
    to derive a hard limit. Capped at 400 words as a practical cover letter ceiling.

    With defaults (12pt Times New Roman, 1.0" top/bottom, 0.8" left/right margins):
      - content_height: 648pt → 43 lines at 15pt line height
      - Minus 8 lines for greeting, signoff, blank separators → 35 usable lines
      - content_width: 496.8pt → ~14.5 words/line at 12pt TNR
      - Budget: 35 × 14.5 × 0.88 safety factor ≈ 446 → capped at 400
    """
    style = get_document_style()
    max_lines = int(style.content_height / style.line_height)
    usable_lines = max_lines - 8  # greeting, signoff, inter-paragraph blank lines
    avg_chars_per_line = style.content_width / (style.font_size * style.char_width_factor)
    avg_words_per_line = avg_chars_per_line / 5.5  # avg word length + space
    budget = int(usable_lines * avg_words_per_line * 0.88)
    return min(budget, 400)


class WriterAgent(BaseAgent[CoverLetterDraft]):
    """
    Writes a cover letter draft using the research brief, candidate profile,
    and tone/structure rules from CoverLetterKit.

    On revision rounds, also receives the previous draft and reviewer feedback.
    """

    agent_name = "writer"
    temperature = 0.35

    def system_prompt(self, **kwargs: Any) -> str:
        word_budget: int = kwargs.get("word_budget", compute_word_budget())
        is_revision: bool = kwargs.get("is_revision", False)
        kit: CoverLetterKit | None = kwargs.get("kit")
        full_name: str = kwargs.get("full_name", "")

        # Pull live rules from the kit so tone/dos/donts/signoff are system instructions,
        # not JSON data. This is what makes the LLM actually follow them.
        if kit:
            tone_line = ", ".join(kit.tone.overall) if kit.tone.overall else ""
            voice_line = " | ".join(kit.tone.voice) if kit.tone.voice else ""
            dislikes_line = "\n".join(f"- {d}" for d in kit.tone.dislikes) if kit.tone.dislikes else ""
            dos_lines = "\n".join(f"- {d}" for d in kit.dos) if kit.dos else ""
            donts_lines = "\n".join(f"- {d}" for d in kit.donts) if kit.donts else ""
            signoff = kit.structure.signoff or "Best regards,"
            # Use up to 3 real style examples as the "GOOD VOICE" anchor
            style_anchor = "\n".join(f'"{ex}"' for ex in (kit.style_examples or [])[:3])
        else:
            tone_line = "direct, grounded, enthusiastic"
            voice_line = "first person | conversational but professional | short clear sentences"
            dislikes_line = "- No em dashes\n- No semicolons\n- No over-the-top hype"
            dos_lines = "- Open with a specific observation about the company\n- Use concrete examples"
            donts_lines = "- Do not invent metrics\n- Do not copy resume bullets verbatim"
            signoff = "Best regards,"
            style_anchor = ""

        base = (
            f"You are a cover letter writer. You MUST respond with valid JSON only. "
            f"No plain text. No code fences. Max {word_budget} words in the letter.\n\n"

            f"CANDIDATE VOICE — write in this voice throughout, every sentence:\n"
            f"Tone: {tone_line}\n"
            f"Voice: {voice_line}\n"
            f"Style dislikes (never use):\n{dislikes_line}\n\n"

            f"GUIDING QUESTIONS — every paragraph must answer at least one:\n"
            f"- SO WHAT? Why does this fact matter to the reader?\n"
            f"- WHY THIS COMPANY? What specifically about their problem?\n"
            f"- WHY YOU? What do you bring that another candidate doesn't?\n"
            f"If a sentence doesn't answer one of these, delete it.\n\n"

            f"CANDIDATE DOS (follow every one):\n{dos_lines}\n\n"

            f"CANDIDATE DON'TS (violating any = failure):\n{donts_lines}\n\n"

            f"CRITICAL RULES:\n"
            f"1. First person ('I built', 'I designed'). Company BY NAME, never 'their mission'.\n"
            f"2. Only facts from verified_experience_bullets or vault_context. No invented metrics.\n"
            f"3. NO REDUNDANCY. If two sentences make the same point, cut one.\n"
            f"4. Never narrate connections. Just describe what you built — the reader connects the dots.\n"
            f"5. Never echo JSON field names ('In my primary experience', 'my matched experience').\n"
            f"6. Specificity rule: use the exact tool names and outcomes from the bullets. "
            f"PERCENTAGES AND NUMBERS: if a bullet contains a specific number (e.g. '500 jobs per minute', "
            f"'3 million rows'), use it exactly. If a bullet has NO number, write NO number — "
            f"describe the outcome in words only. It is NEVER acceptable to write a percentage "
            f"(like '25%' or '30%') that does not appear verbatim in the verified_experience_bullets.\n\n"

            f"BANNED PHRASES (instant fail — using any of these in the letter = 0 on Voice):\n"
            + "".join(f"- '{p}'\n" for p in BANNED_PHRASES)
            + "\n"

            f"STRUCTURE — content_md must contain exactly these parts in order:\n"
            f"1. 'Dear Hiring Manager,'\n"
            f"2. Opener (2-3 sentences): Name a SPECIFIC technical problem the company solves. "
            f"Connect it immediately to something you built. Why this company, why now, why you?\n"
            f"3. Impact (2-3 sentences): Strongest experience. Lead with the outcome or insight, "
            f"not the task. One concrete metric.\n"
            f"4. Supporting (1-2 sentences): ONE different skill. Must not overlap with Impact.\n"
            f"5. Close (1-2 sentences): Confident, specific. Name what you'd build. No filler.\n"
            f"6. '{signoff}' then a blank line then the candidate's name from candidate.name "
            f"(or as overridden by user_instructions if the user asked to change it).\n\n"
        )

        if style_anchor:
            base += (
                f"VOICE ANCHOR — these sentences are from the candidate's best past letters. "
                f"Match this exact register:\n{style_anchor}\n\n"
            )

        base += (
            f"BAD OPENER: 'Quantcast processes billions of data points. I want to be part of that.'\n"
            f"WHY BAD: Vague desire, no personal connection.\n"
            f"GOOD OPENER: 'Quantcast turns raw bidstream data into real-time audience models — "
            f"the kind of feature engineering problem I spent six months solving when I built "
            f"anomaly detection on 3M rows of device telemetry at Johnson Controls.'\n"
            f"WHY GOOD: Specific problem + immediate personal connection.\n\n"
        )

        if is_revision:
            base += (
                "You are revising a previous draft. Address each feedback item specifically. "
                "Keep what is working. Do not rewrite sections with no feedback.\n\n"
            )

        base += (
            "Respond ONLY with valid JSON:\n"
            '{"content_md": "full letter from Dear Hiring Manager through signoff and name", '
            '"sections_used": ["opener", "impact", "fit", "close"], '
            '"word_count": 350}\n'
            "Do not wrap in code fences."
        )
        return base

    def build_user_message(self, **kwargs: Any) -> str:
        research_brief: ResearchBrief = kwargs["research_brief"]
        profile: ProfileBundle = kwargs["profile"]
        kit: CoverLetterKit = kwargs["kit"]
        word_budget: int = kwargs.get("word_budget", compute_word_budget())
        is_revision: bool = kwargs.get("is_revision", False)
        previous_draft: CoverLetterDraft | None = kwargs.get("previous_draft")
        reviewer_feedback: ReviewVerdict | None = kwargs.get("reviewer_feedback")
        user_notes: list[str] = kwargs.get("user_notes", [])
        # Keys resolved by coordinator — only verified bullets for these experiences reach the writer
        matched_experience_keys: list[str] = kwargs.get("matched_experience_keys", [])

        # Filter kit experience to only the researcher-selected entries.
        # If no keys (e.g. fallback path), send all experiences.
        if matched_experience_keys:
            key_set = set(matched_experience_keys)
            selected_experiences = [e for e in kit.experience if e.key in key_set]
            # Preserve researcher's ordering: primary key first
            selected_experiences.sort(key=lambda e: matched_experience_keys.index(e.key))
        else:
            selected_experiences = kit.experience

        payload: dict[str, Any] = {}

        # User instructions go FIRST so the LLM sees them before the data payload.
        # These must override anything in the data below (name, formatting, etc.).
        if user_notes:
            payload["user_instructions"] = user_notes

        payload.update({
            "word_budget": word_budget,
            "role": {
                "company": research_brief.company_name,
                "company_context": research_brief.company_context,
                "themes": research_brief.role_themes,
                "requirements": research_brief.jd_requirements[:6],
            },
            "candidate": {
                "name": profile.full_name,
                "primary_experience": research_brief.primary_experience,
                "suggested_project": research_brief.suggested_project,
                "vault_context": research_brief.vault_excerpts[:3],
            },
            # AUTHORITATIVE source: verified bullets from the kit for researcher-selected experiences.
            # These are the ONLY facts the writer may use. Do not infer beyond them.
            "verified_experience_bullets": [
                {"title": exp.title, "bullets": exp.bullets}
                for exp in selected_experiences
            ],
            "opener_guidance": kit.structure.opener_guidance,
            "impact_samples": kit.structure.impact.samples[:3],
            "memory_notes": research_brief.memory_notes,
        })

        if is_revision and previous_draft and reviewer_feedback:
            payload["revision"] = {
                "previous_draft": previous_draft.content_md,
                "feedback_to_address": reviewer_feedback.feedback,
                "strengths_to_keep": reviewer_feedback.strengths,
                "overall_impression": reviewer_feedback.overall_impression,
            }

        return json.dumps(payload, ensure_ascii=False)

    def parse_response(self, raw: dict[str, Any]) -> CoverLetterDraft:
        content = raw.get("content_md", "")
        declared_count = raw.get("word_count")
        actual_count = len(content.split()) if content else 0
        word_count = int(declared_count) if declared_count else actual_count

        return CoverLetterDraft(
            version=0,   # version number set by PipelineCoordinator
            content_md=content,
            word_count=word_count,
            sections_used=list(raw.get("sections_used", [])),
        )
