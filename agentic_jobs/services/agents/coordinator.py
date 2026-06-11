from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_jobs.config import settings
from agentic_jobs.core.enums import (
    ApplicationStage,
    ArtifactType,
    PipelineMode,
    PipelineStatus,
)
from agentic_jobs.db import models
from agentic_jobs.services.agents.graph.pipeline_graph import PIPELINE_GRAPH
from agentic_jobs.services.agents.graph.state import PipelineState
from agentic_jobs.services.agents.schemas import (
    CoverLetterDraft,
    PipelineResult,
    ResearchBrief,
    ReviewVerdict,
)
from agentic_jobs.services.agents.writer import WriterAgent, compute_word_budget
from agentic_jobs.services.applications.stage import apply_stage
from agentic_jobs.services.artifacts.utils import ARTIFACTS_DIR, load_artifact_text
from agentic_jobs.services.llm.prompt_builder import (
    ProfileBundle,
    STACK_DEFAULTS,
)
from agentic_jobs.services.llm.runner import LlmBackendError
from agentic_jobs.services.llm.style_kit import CoverLetterKit, load_cover_letter_kit
from agentic_jobs.services.research.cache import CompanyResearchCache
from agentic_jobs.services.research.domains import build_research_urls, extract_domain
from agentic_jobs.services.research.scraper import CompanyScraper
from agentic_jobs.services.slack.client import SlackClient
from agentic_jobs.services.vault.embedder import VaultEmbedder
from agentic_jobs.services.vault.parser import VaultParser
from agentic_jobs.services.vault.retriever import VaultRetriever
from agentic_jobs.services.vault.graph import WikilinkGraph


LOGGER = logging.getLogger(__name__)

_JD_REQUIREMENTS_MARKERS = (
    "qualifications", "requirements", "you will", "what you'll",
    "responsibilities", "what we're looking for", "about you",
)

# How many chars to extract from the requirements section for the vault query.
_JD_QUERY_EXCERPT_LEN = 600
# Don't match a marker if fewer than this many chars remain — likely a false positive near EOF.
_JD_QUERY_MIN_REMAINING = 100
# Fallback slice when no requirements marker is found: skip the typical intro paragraph.
_JD_QUERY_FALLBACK_START = 200
_JD_QUERY_FALLBACK_END = 800


def _vault_query_from_jd(jd_text: str) -> str:
    """Extract requirements-section text from JD for vault search.

    JDs typically open with 2-3 paragraphs of company marketing copy.
    Searching that finds generic content, not candidate-relevant context.
    This skips to the requirements/qualifications section instead.
    """
    lower = jd_text.lower()
    for marker in _JD_REQUIREMENTS_MARKERS:
        idx = lower.find(marker)
        if 0 < idx < len(jd_text) - _JD_QUERY_MIN_REMAINING:
            return jd_text[idx:idx + _JD_QUERY_EXCERPT_LEN]
    # Fallback: skip likely intro paragraph
    return jd_text[_JD_QUERY_FALLBACK_START:_JD_QUERY_FALLBACK_END]


class PipelineCoordinatorError(RuntimeError):
    """Raised when the pipeline cannot proceed."""


class PipelineCoordinator:
    """
    Orchestrates the full multi-agent cover letter generation pipeline:

        Research → Write → [Review → Revise]* → Persist → Notify

    Flow:
      1. Gather data in parallel: scrape company site, search vault, load memory
      2. ResearcherAgent synthesizes data into a ResearchBrief
      3. WriterAgent produces CoverLetterDraft v1
      4. HiringManagerAgent reviews draft with full context (brief + JD + tone rules)
      5. If score < threshold and revisions remaining: WriterAgent revises
      6. Persist artifact + PipelineRun record
      7. Post progress and final draft to Slack thread

    This coordinator is intentionally parallel to DraftGenerator — it accepts the
    same application_id and returns a result that maps cleanly to DraftResult.
    DraftGenerator continues to handle Quick Draft (single-pass) mode.
    """

    def __init__(
        self,
        session: Session,
        slack_client: SlackClient | None = None,
    ) -> None:
        self.session = session
        self.slack_client = slack_client
        self._kit: CoverLetterKit | None = None
        self._scraper = CompanyScraper()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(
        self,
        application_id: UUID,
        *,
        notes: list[str] | None = None,
        author: str | None = None,
        post_to_slack: bool = False,
    ) -> PipelineResult:
        started_at = time.monotonic()
        application = self._ensure_application(application_id)
        job = application.job
        if job is None:
            raise PipelineCoordinatorError("Application missing job reference.")

        kit = self._load_kit()
        profile = self._build_profile_bundle()
        word_budget = compute_word_budget()
        company_domain = self._resolve_company_domain(job)
        agent_log: list[dict] = []

        # Create PipelineRun record
        run_record = models.PipelineRun(
            application_id=application_id,
            mode=PipelineMode.FULL_PIPELINE,
            status=PipelineStatus.RUNNING,
            agent_log=[],
        )
        self.session.add(run_record)
        # Mark in-progress immediately so the tracker reflects it before the LLM work starts
        apply_stage(application, ApplicationStage.COVER_LETTER_IN_PROGRESS)
        self.session.commit()
        # Capture the ID now — after commit SQLAlchemy expires all attributes including PKs,
        # so accessing run_record.id later would trigger a lazy-load on a potentially
        # detached session and raise DetachedInstanceError.
        pipeline_run_id = run_record.id
        await self._refresh_tracker()

        try:
            # ----------------------------------------------------------------
            # Phase 1: Data gathering
            # ----------------------------------------------------------------
            await self._post_progress(application, post_to_slack, f"_Researching {job.company_name}..._")

            scraped_pages = await self._gather_company_data(
                job.company_name, company_domain, application_id
            )

            if not scraped_pages and not company_domain:
                CompanyResearchCache(self.session).write_no_domain_note(job.company_name)

            vault_matches = await self._search_vault(job.jd_text)
            memory_notes = self._load_memory_notes()

            from agentic_jobs.services.agents.guardrails import sanitize
            clean_notes = [sanitize(n, source="slack:user_note") for n in (notes or []) if n]

            await self._post_progress(application, post_to_slack, "_Research complete. Writing draft..._")

            # ----------------------------------------------------------------
            # Phases 2-3: Research synthesis + Write/Review loop (LangGraph)
            # ----------------------------------------------------------------
            initial_state: PipelineState = {
                "application_id": application_id,
                "job_id": job.id,
                "company_name": job.company_name,
                "company_domain": company_domain,
                "jd_text": job.jd_text,
                "role_title": job.title,
                "profile": profile,
                "kit": kit,
                "word_budget": word_budget,
                "pass_threshold": settings.pipeline_pass_threshold,
                "max_revisions": settings.pipeline_max_revisions,
                "notes": notes or [],
                "clean_notes": clean_notes,
                "author": author,
                "post_to_slack": post_to_slack,
                "scraped_pages": scraped_pages,
                "vault_matches": vault_matches,
                "memory_notes": memory_notes,
                "research_brief": None,
                "ordered_keys": [],
                "draft": None,
                "review_history": [],
                "revision_round": 0,
                "agent_log": agent_log,
                "started_at": started_at,
            }

            final_state: PipelineState = await PIPELINE_GRAPH.ainvoke(initial_state)

            research_brief: ResearchBrief = final_state["research_brief"]
            draft: CoverLetterDraft | None = final_state["draft"]
            review_history: list[ReviewVerdict] = final_state["review_history"]
            agent_log = final_state["agent_log"]

            # Write intelligence notes to Obsidian — candidate reference only, not used by writer
            if research_brief.company_intelligence and company_domain:
                try:
                    CompanyResearchCache(self.session).write_intelligence_to_vault(
                        job.company_name, company_domain, research_brief.company_intelligence
                    )
                except Exception as exc:
                    LOGGER.warning("[coordinator] Failed to write company intelligence to vault: %s", exc)
                    agent_log.append({"phase": "vault_write", "error": str(exc)})

            assert draft is not None

            # ----------------------------------------------------------------
            # Phase 4: Persist
            # ----------------------------------------------------------------
            # Lock the application row before counting versions so two concurrent
            # pipelines for the same application can't both read count=0 and both
            # write cl-v1.md, overwriting each other.
            self.session.execute(
                select(models.Application.id)
                .where(models.Application.id == application_id)
                .with_for_update()
            )
            version_number = self._count_cover_letter_versions(application_id) + 1
            uri = self._write_artifact(application, version_number, draft.content_md)

            # Persist assistant feedback note (same as DraftGenerator)
            from agentic_jobs.core.enums import FeedbackRole
            self.session.add(models.ApplicationFeedback(
                application_id=application_id,
                role=FeedbackRole.ASSISTANT,
                text=draft.content_md,
            ))

            # Persist user notes
            if clean_notes:
                for note in clean_notes:
                    self.session.add(models.ApplicationFeedback(
                        application_id=application_id,
                        role=FeedbackRole.USER,
                        author=author,
                        text=note,
                    ))

            # Update PipelineRun
            final_score = review_history[-1].score if review_history else None
            run_record.status = PipelineStatus.COMPLETED
            run_record.agent_log = agent_log
            run_record.final_score = final_score
            run_record.revision_count = len(review_history) - 1
            run_record.finished_at = datetime.now(timezone.utc)

            self.session.commit()

            total_ms = int((time.monotonic() - started_at) * 1000)

            # ----------------------------------------------------------------
            # Phase 5: Slack notification
            # ----------------------------------------------------------------
            if post_to_slack:
                summary = (
                    f"*Cover letter ready* | Score: {final_score}/10 | "
                    f"{draft.word_count} words | {len(review_history)} round(s)\n\n"
                    + draft.content_md
                )
                await self._post_to_thread(application, summary)

            return PipelineResult(
                final_draft=draft,
                research_brief=research_brief,
                review_history=review_history,
                pipeline_run_id=pipeline_run_id,
                total_duration_ms=total_ms,
            )

        except Exception as exc:
            # Roll back any partial transaction before writing failure state.
            # A failed mid-pipeline commit (e.g. line 357) leaves the session
            # in an invalid state; committing without rollback first raises
            # InvalidRequestError and loses the original error.
            try:
                self.session.rollback()
            except Exception:
                pass
            run_record.status = PipelineStatus.FAILED
            run_record.agent_log = agent_log
            run_record.finished_at = datetime.now(timezone.utc)
            try:
                self.session.commit()
            except Exception:
                LOGGER.exception("[coordinator] Failed to persist pipeline failure state for run %s", pipeline_run_id)
            raise PipelineCoordinatorError(f"Pipeline failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Revision-only path (no researcher, no review loop)
    # ------------------------------------------------------------------

    async def run_revision(
        self,
        application_id: UUID,
        *,
        notes: list[str],
        author: str | None = None,
        post_to_slack: bool = False,
    ) -> CoverLetterDraft:
        """Re-run only the WriterAgent against the existing draft with user feedback.

        Used when thread feedback is a content edit ("make the opener more specific")
        rather than a full re-research request. Skips the ResearcherAgent and the
        review loop — the writer revises in one pass and the result is persisted.

        Falls back to run() if no previous draft exists.
        """
        from agentic_jobs.core.enums import FeedbackRole
        from agentic_jobs.services.agents.guardrails import sanitize

        application = self._ensure_application(application_id)
        job = application.job
        if job is None:
            raise PipelineCoordinatorError("Application missing job reference.")

        previous_text = load_artifact_text(self.session, application_id, ArtifactType.COVER_LETTER_VERSION)
        if not previous_text:
            LOGGER.info("[coordinator] No previous draft for %s — falling back to full pipeline", application_id)
            return (await self.run(application_id, notes=notes, author=author, post_to_slack=post_to_slack)).final_draft

        apply_stage(application, ApplicationStage.COVER_LETTER_IN_PROGRESS)
        self.session.commit()
        await self._refresh_tracker()

        kit = self._load_kit()
        profile = self._build_profile_bundle()
        word_budget = compute_word_budget()
        clean_notes = [sanitize(n, source="slack:user_note") for n in notes if n]

        previous_draft = CoverLetterDraft(
            version=0,
            content_md=previous_text,
            word_count=len(previous_text.split()),
        )
        # Treat user notes as reviewer feedback so the writer knows what to change
        user_verdict = ReviewVerdict(
            score=0.0,
            verdict="revise",
            overall_impression="User requested changes via thread",
            feedback=clean_notes,
            strengths=["Keep what is not mentioned in the feedback unchanged"],
            areas_for_improvement=clean_notes,
        )
        # Minimal brief — company name from DB, no fresh research needed
        research_brief = ResearchBrief(
            company_name=job.company_name,
            company_domain=extract_domain(job.company_website) if job.company_website else "",
            company_context="",
            role_themes=[],
            jd_requirements=[],
            matched_experiences=[],
            primary_experience="",
            vault_excerpts=[],
            memory_notes=[],
            suggested_project="",
        )

        writer = WriterAgent()
        draft = await writer.run(
            research_brief=research_brief,
            profile=profile,
            kit=kit,
            full_name=profile.full_name,
            word_budget=word_budget,
            matched_experience_keys=[],  # all experiences available to writer
            is_revision=True,
            previous_draft=previous_draft,
            reviewer_feedback=user_verdict,
            user_notes=clean_notes,
        )
        draft.version = self._count_cover_letter_versions(application_id) + 1

        uri = self._write_artifact(application, draft.version, draft.content_md)
        self.session.add(models.ApplicationFeedback(
            application_id=application_id,
            role=FeedbackRole.ASSISTANT,
            text=draft.content_md,
        ))
        if clean_notes:
            for note in clean_notes:
                self.session.add(models.ApplicationFeedback(
                    application_id=application_id,
                    role=FeedbackRole.USER,
                    author=author,
                    text=note,
                ))
        self.session.commit()

        if post_to_slack:
            await self._post_to_thread(
                application,
                f"*Revised draft* (v{draft.version}) | {draft.word_count} words\n\n{draft.content_md}",
            )

        return draft

    # ------------------------------------------------------------------
    # Data gathering helpers
    # ------------------------------------------------------------------

    async def _gather_company_data(
        self, company_name: str, domain: str | None, application_id: UUID
    ) -> list:
        from agentic_jobs.services.research.scraper import ScrapedPage
        if not domain:
            LOGGER.warning(
                "[coordinator] No company_website for %s — skipping research scrape. "
                "Re-run discovery after this job's ATS page is re-ingested.",
                company_name,
            )
            return []

        cache = CompanyResearchCache(self.session)
        cached = cache.get(domain)
        if cached:
            LOGGER.info("[coordinator] Using cached company data for %s", domain)
            pages_data = cached.get("pages", [])
            return [
                ScrapedPage(
                    url=p["url"], title=p.get("title", ""),
                    text=p.get("text", ""), status_code=p.get("status_code", 200)
                )
                for p in pages_data if p.get("text")
            ]

        urls = build_research_urls(company_name, domain)
        if not urls:
            LOGGER.info("[coordinator] No scrapable URLs for domain %s", domain)
            return []

        pages = await self._scraper.scrape(urls)
        pages_with_content = [p for p in pages if p.text]
        if pages_with_content:
            cache.put(domain, company_name, pages_with_content)
        return pages_with_content

    async def _search_vault(self, jd_text: str) -> list:
        if not settings.vault_path:
            return []
        try:
            vault_path = Path(settings.vault_path)
            parser = VaultParser(vault_path)
            sections = parser.parse_all()
            graph = WikilinkGraph(sections)
            retriever = VaultRetriever(self.session, graph)
            query = _vault_query_from_jd(jd_text)
            return await retriever.search(query)
        except Exception as exc:
            LOGGER.warning("[coordinator] Vault search failed: %s", exc)
            return []

    def _load_memory_notes(self) -> list[str]:
        """Load recent long-term memory notes. Populated by Phase 5 (MemoryStore)."""
        from agentic_jobs.core.enums import MemoryType
        try:
            rows = self.session.execute(
                select(models.AgentMemory.content)
                .where(models.AgentMemory.memory_type == MemoryType.LONG_TERM)
                .order_by(models.AgentMemory.created_at.desc())
                .limit(5)
            ).scalars().all()
            return list(rows)
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Artifact + profile helpers (mirrors DraftGenerator)
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_company_domain(job: models.Job) -> str | None:
        """
        Return the best available company domain for research.

        Preference order:
          1. job.company_website  — set by the discovery adapters from LD+JSON,
             OG tags, external links, or subdomain stripping (post-fix jobs)
          2. job.domain_root fallback — for jobs ingested before the extraction
             fix. Strip known job-related subdomain prefixes; skip pure ATS
             domains since we cannot derive the company website from them.
        """
        from agentic_jobs.services.research.domains import (
            _is_third_party_domain,
            _strip_job_subdomains,
        )

        if job.company_website:
            return extract_domain(job.company_website)

        if not job.domain_root:
            return None

        if _is_third_party_domain(job.domain_root):
            return None

        return _strip_job_subdomains(job.domain_root)

    def _ensure_application(self, application_id: UUID) -> models.Application:
        app = self.session.get(models.Application, application_id)
        if app is None:
            raise PipelineCoordinatorError("Application not found.")
        return app

    def _load_kit(self) -> CoverLetterKit:
        if self._kit is None:
            self._kit = load_cover_letter_kit()
        return self._kit

    def _build_profile_bundle(self) -> ProfileBundle:
        identity = self.session.execute(
            select(models.ProfileIdentity).limit(1)
        ).scalar_one_or_none()
        kit = self._load_kit()

        if identity is None:
            return ProfileBundle(
                full_name=settings.profile_fallback_name,
                preferred_name=None,
                email=None,
                phone=None,
                base_location=None,
                links={},
                skills=sum(kit.profile.technical_strengths.values(), []),
                stack=STACK_DEFAULTS,
                projects=[
                    {"name": p.name, "one_liner": p.summary,
                     "metric": p.talking_points[0] if p.talking_points else ""}
                    for p in kit.projects
                ],
            )

        links = {}
        if identity.links:
            if identity.links.linkedin:
                links["linkedin"] = identity.links.linkedin
            if identity.links.github:
                links["github"] = identity.links.github
            if identity.links.portfolio:
                links["portfolio"] = identity.links.portfolio

        return ProfileBundle(
            full_name=identity.name,
            preferred_name=identity.preferred_name,
            email=identity.email,
            phone=identity.phone,
            base_location=identity.base_location,
            links=links,
            skills=identity.facts.skills if identity.facts else [],
            stack=identity.facts.tools if identity.facts else [],
            projects=[
                {"name": p.name, "one_liner": p.summary,
                 "metric": p.talking_points[0] if p.talking_points else ""}
                for p in kit.projects
            ],
        )

    def _write_artifact(
        self, application: models.Application, version_number: int, letter: str
    ) -> str:
        artifact_dir = ARTIFACTS_DIR / application.human_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        path = artifact_dir / f"cl-v{version_number}.md"
        path.write_text(letter, encoding="utf-8")
        uri = f"file://{path.resolve()}"
        self.session.add(models.Artifact(
            application_id=application.id,
            type=ArtifactType.COVER_LETTER_VERSION,
            uri=uri,
        ))
        return uri

    def _count_cover_letter_versions(self, application_id: UUID) -> int:
        from sqlalchemy import func
        stmt = (
            select(func.count())
            .select_from(models.Artifact)
            .where(
                models.Artifact.application_id == application_id,
                models.Artifact.type == ArtifactType.COVER_LETTER_VERSION,
            )
        )
        return self.session.execute(stmt).scalar_one() or 0

    # ------------------------------------------------------------------
    # Slack helpers
    # ------------------------------------------------------------------

    async def _post_progress(
        self, application: models.Application, enabled: bool, message: str
    ) -> None:
        if not enabled or not self.slack_client:
            return
        if not application.slack_channel_id or not application.slack_thread_ts:
            return
        try:
            await self.slack_client.post_thread_message(
                channel=application.slack_channel_id,
                thread_ts=application.slack_thread_ts,
                text=message,
            )
        except Exception:
            LOGGER.warning("[coordinator] Failed to post progress to Slack")

    async def _post_to_thread(self, application: models.Application, text: str) -> None:
        await self._post_progress(application, True, text)

    async def _refresh_tracker(self) -> None:
        if not self.slack_client or not settings.slack_jobs_tracker_channel:
            return
        from agentic_jobs.services.slack.tracker import MasterTracker
        try:
            await MasterTracker(self.session, self.slack_client).refresh()
        except Exception:
            LOGGER.warning("[coordinator] Failed to refresh master tracker")
