from __future__ import annotations

import json
import logging
from typing import Any

from agentic_jobs.services.agents.base import BaseAgent
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

        base = (
            f"You are a cover letter writer. You MUST respond with valid JSON only. "
            f"No plain text. No code fences. Max {word_budget} words in the letter.\n\n"

            f"GUIDING QUESTIONS — every paragraph you write must answer these:\n"
            f"- SO WHAT? Why does this fact matter to the reader? Don't just state what you did.\n"
            f"- WHY THIS COMPANY? What specifically about their problem excites you and why?\n"
            f"- WHY YOU? What do you bring that another candidate doesn't?\n"
            f"- WHAT DID YOU LEARN? Show growth, not just output.\n"
            f"If a sentence doesn't answer at least one of these, delete it.\n\n"

            f"CRITICAL RULES (violating any = failure):\n"
            f"1. First person ('I built', 'I designed'). Company BY NAME ('Quantcast is building'). "
            f"NEVER 'their mission', 'their work', 'the company'.\n"
            f"2. Only facts from verified_experience_bullets or vault_context. No invented metrics.\n"
            f"3. NO REDUNDANCY. Never repeat the same idea, phrase, or keyword twice. "
            f"Read every sentence and ask: did I already say this? If two sentences make the "
            f"same point, cut one. 'Improved chatbot reliability... improved chatbot reliability' = fail.\n"
            f"4. Never narrate connections or explain why something fits. "
            f"BAD: 'This is a natural fit for X because it requires analyzing...' "
            f"BAD: 'This experience will enable me to contribute to...' "
            f"BAD: 'leveraging my expertise in X to drive impact at Y' "
            f"GOOD: Just describe what you built. The reader connects the dots.\n"
            f"5. Never echo JSON field names ('In my primary experience', 'my matched experience').\n"
            f"6. Every claim needs a concrete detail — a number, a tool name, or a specific outcome. "
            f"No vague phrases like 'complex patterns' or 'meaningful impact'.\n\n"

            f"BANNED PHRASES (use of any = instant fail):\n"
            f"- 'I am drawn to', 'I am excited to', 'I am interested in'\n"
            f"- 'Their work', 'Their mission', 'They are'\n"
            f"- 'I look forward to discussing', 'how my experiences align'\n"
            f"- 'your company's goals', 'I would love the opportunity'\n"
            f"- 'I believe my skills make me a strong fit'\n"
            f"- 'This demonstrates my ability to', 'This work is similar to'\n"
            f"- 'a natural fit for', 'This experience will enable me to'\n"
            f"- 'leveraging my expertise', 'drive meaningful impact'\n"
            f"- 'I want to be part of' (too vague — say WHY)\n\n"

            f"STRUCTURE:\n"
            f"1. 'Dear Hiring Manager,'\n"
            f"2. Opener (2-3 sentences): Name a SPECIFIC technical problem the company is solving "
            f"(not a generic mission statement). Then say why YOU care — connect it to something "
            f"you've built or struggled with. Answer: why this company, why now, why you?\n"
            f"3. Impact (2-3 sentences): Your strongest experience. Lead with the outcome or "
            f"insight, not the task. Use one concrete metric. Answer: so what?\n"
            f"4. Supporting (1-2 sentences): ONE different skill or experience that adds a new "
            f"dimension. Must not overlap with Impact. Answer: what else do you bring?\n"
            f"5. Close (1-2 sentences): Confident, specific. Name what you'd do or build. "
            f"No filler, no begging, no generic 'impact' language.\n"
            f"6. 'Best regards,'\n\n"

            f"BAD OPENER: 'Quantcast processes billions of data points daily to help businesses "
            f"understand consumer behavior. I want to be part of building that infrastructure.'\n"
            f"WHY IT'S BAD: States a fact about the company + a vague desire. Doesn't answer "
            f"'why you?' or 'so what?'\n"
            f"GOOD OPENER: 'Quantcast turns raw bidstream data into real-time audience models — "
            f"the kind of feature engineering problem I spent six months solving when I built "
            f"anomaly detection on 3M rows of device telemetry at Johnson Controls.'\n"
            f"WHY IT'S GOOD: Names a specific technical challenge, immediately connects to "
            f"candidate's own work, reader sees the fit without being told.\n\n"

            f"BAD CLOSE: 'I would bring both the data engineering depth and the builder mindset "
            f"that this role calls for.'\n"
            f"WHY IT'S BAD: Generic — could be pasted into any letter. Says nothing specific.\n"
            f"GOOD CLOSE: 'I want to build the pipeline that turns Quantcast's bidstream "
            f"into the features that win auctions.'\n"
            f"WHY IT'S GOOD: Names a specific thing the candidate wants to build at THIS company.\n\n"
        )

        if is_revision:
            base += (
                "You are revising a previous draft based on hiring manager feedback. "
                "Address each feedback item specifically. Keep what is working (see strengths). "
                "Do not rewrite sections that received no feedback.\n\n"
            )

        base += (
            "Respond ONLY with valid JSON:\n"
            '{"content_md": "the full cover letter text", '
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

        payload: dict[str, Any] = {
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
                "matched_experiences": research_brief.matched_experiences,
                "suggested_project": research_brief.suggested_project,
                "vault_context": research_brief.vault_excerpts[:3],
            },
            "verified_experience_bullets": [
                {"title": exp.title, "bullets": exp.bullets}
                for exp in kit.experience
            ],
            "tone_rules": {
                "overall": kit.tone.overall,
                "voice": kit.tone.voice,
                "likes": kit.tone.likes,
                "dislikes": kit.tone.dislikes,
                "dos": kit.dos,
                "donts": kit.donts,
            },
            "structure": {
                "greeting": kit.structure.greeting,
                "opener_guidance": kit.structure.opener_guidance,
                "impact_samples": kit.structure.impact.samples[:3],
                "plan_bullets": kit.structure.plan.bullets[:3],
                "signoff": kit.structure.signoff,
            },
            "style_examples": kit.style_examples[:2],
            "memory_notes": research_brief.memory_notes,
        }

        if user_notes:
            payload["user_instructions"] = user_notes

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
