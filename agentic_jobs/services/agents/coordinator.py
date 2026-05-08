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
from agentic_jobs.services.agents.researcher import ResearcherAgent
from agentic_jobs.services.agents.reviewer import HiringManagerAgent
from agentic_jobs.services.agents.schemas import (
    CoverLetterDraft,
    PipelineResult,
    ResearchBrief,
    ReviewVerdict,
)
from agentic_jobs.services.agents.writer import WriterAgent, compute_word_budget
from agentic_jobs.services.applications.stage import apply_stage
from agentic_jobs.services.artifacts.utils import ARTIFACTS_DIR
from agentic_jobs.services.llm.prompt_builder import (
    ProfileBundle,
    STACK_DEFAULTS,
    build_prompt_payload,
    DraftContext,
    FeedbackNote,
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
        company_domain = extract_domain(job.url)
        agent_log: list[dict] = []

        # Create PipelineRun record
        run_record = models.PipelineRun(
            application_id=application_id,
            mode=PipelineMode.FULL_PIPELINE,
            status=PipelineStatus.RUNNING,
            agent_log=[],
        )
        self.session.add(run_record)
        self.session.flush()  # get the ID

        try:
            # ----------------------------------------------------------------
            # Phase 1: Data gathering
            # ----------------------------------------------------------------
            await self._post_progress(application, post_to_slack, f"_Researching {job.company_name}..._")

            scraped_pages = await self._gather_company_data(
                job.company_name, company_domain, application_id
            )
            vault_matches = await self._search_vault(job.jd_text)
            memory_notes = self._load_memory_notes()

            agent_log.append({
                "phase": "data_gathering",
                "scraped_pages": len(scraped_pages),
                "vault_matches": len(vault_matches),
                "memory_notes": len(memory_notes),
            })

            # ----------------------------------------------------------------
            # Phase 2: Research synthesis
            # ----------------------------------------------------------------
            researcher = ResearcherAgent()
            t0 = time.monotonic()
            research_brief: ResearchBrief = await researcher.run(
                jd_text=job.jd_text,
                company_name=job.company_name,
                scraped_pages=scraped_pages,
                vault_matches=vault_matches,
                profile=profile,
                kit=kit,
                memory_notes=memory_notes,
            )
            # Attach raw vault text for writer/reviewer context
            research_brief.company_domain = company_domain
            research_brief.company_name = research_brief.company_name or job.company_name
            research_brief.vault_excerpts = [m.text for m in vault_matches[:4]]

            agent_log.append({
                "phase": "researcher",
                "duration_ms": int((time.monotonic() - t0) * 1000),
                "themes": research_brief.role_themes,
                "suggested_project": research_brief.suggested_project,
            })
            await self._post_progress(application, post_to_slack, "_Research complete. Writing draft..._")

            # ----------------------------------------------------------------
            # Phase 3: Write + Review loop
            # ----------------------------------------------------------------
            writer = WriterAgent()
            reviewer = HiringManagerAgent()
            draft: CoverLetterDraft | None = None
            review_history: list[ReviewVerdict] = []
            from agentic_jobs.services.agents.guardrails import sanitize
            clean_notes = [sanitize(n, source="slack:user_note") for n in (notes or []) if n]
            pass_threshold = settings.pipeline_pass_threshold
            max_revisions = settings.pipeline_max_revisions

            for revision_round in range(max_revisions + 1):
                is_revision = revision_round > 0
                t0 = time.monotonic()

                draft = await writer.run(
                    research_brief=research_brief,
                    profile=profile,
                    kit=kit,
                    word_budget=word_budget,
                    is_revision=is_revision,
                    previous_draft=draft if is_revision else None,
                    reviewer_feedback=review_history[-1] if is_revision else None,
                    user_notes=clean_notes,
                )
                draft.version = revision_round + 1

                agent_log.append({
                    "phase": f"writer_round_{draft.version}",
                    "duration_ms": int((time.monotonic() - t0) * 1000),
                    "word_count": draft.word_count,
                })

                await self._post_progress(
                    application, post_to_slack,
                    f"_Draft v{draft.version} written ({draft.word_count} words). Reviewing..._"
                )

                # Review
                t0 = time.monotonic()
                verdict: ReviewVerdict = await reviewer.run(
                    draft=draft,
                    research_brief=research_brief,
                    jd_text=job.jd_text,
                    kit=kit,
                    role_title=job.title,
                    company_name=job.company_name,
                    round_number=revision_round + 1,
                )
                review_history.append(verdict)

                agent_log.append({
                    "phase": f"reviewer_round_{draft.version}",
                    "duration_ms": int((time.monotonic() - t0) * 1000),
                    "score": verdict.score,
                    "verdict": verdict.verdict,
                })

                # Always do at least 2 revisions (3 total writer calls).
                # Rounds 0 and 1 always revise regardless of score.
                if revision_round > 1 and (verdict.verdict == "pass" or verdict.score >= pass_threshold):
                    LOGGER.info("[coordinator] Draft passed review: score=%.1f", verdict.score)
                    break

                if revision_round < max_revisions:
                    await self._post_progress(
                        application, post_to_slack,
                        f"_Score: {verdict.score}/10. Revising (round {revision_round + 2}/{max_revisions + 1})..._"
                    )
                else:
                    LOGGER.info(
                        "[coordinator] Max revisions reached. Accepting best draft (score=%.1f)", verdict.score
                    )

            assert draft is not None

            # ----------------------------------------------------------------
            # Phase 4: Persist
            # ----------------------------------------------------------------
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

            apply_stage(application, ApplicationStage.COVER_LETTER_IN_PROGRESS)

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
                pipeline_run_id=run_record.id,
                total_duration_ms=total_ms,
            )

        except Exception as exc:
            run_record.status = PipelineStatus.FAILED
            run_record.agent_log = agent_log
            run_record.finished_at = datetime.now(timezone.utc)
            self.session.commit()
            raise PipelineCoordinatorError(f"Pipeline failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Data gathering helpers
    # ------------------------------------------------------------------

    async def _gather_company_data(
        self, company_name: str, domain: str, application_id: UUID
    ) -> list:
        from agentic_jobs.services.research.scraper import ScrapedPage
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
            # Use key terms from JD as search query
            query = jd_text[:500]
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
