from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from agentic_jobs.core.enums import ApplicationStatus, ArtifactType, FeedbackRole
from agentic_jobs.db import models
from agentic_jobs.services.llm.prompt_builder import (
    DraftContext,
    FeedbackNote,
    ProfileBundle,
    build_prompt_payload,
)
from agentic_jobs.services.llm.runner import LlmResponse, generate_cover_letter, summarize_feedback
from agentic_jobs.services.llm.style_kit import CoverLetterKit, load_cover_letter_kit
from agentic_jobs.services.slack.client import SlackClient


ARTIFACTS_DIR = Path("artifacts")


@dataclass(slots=True)
class DraftResult:
    application_id: UUID
    human_id: str
    version: str
    cover_letter_md: str
    artifact_uri: str
    payload: dict


class DraftGeneratorError(RuntimeError):
    """Raised when draft generation fails."""


class DraftGenerator:
    def __init__(self, session: Session, slack_client: SlackClient | None = None) -> None:
        self.session = session
        self.slack_client = slack_client
        self._kit: CoverLetterKit | None = None

    def _load_kit(self) -> CoverLetterKit:
        if self._kit is None:
            self._kit = load_cover_letter_kit()
        return self._kit

    def _ensure_application(self, application_id: UUID) -> models.Application:
        app = self.session.get(models.Application, application_id)
        if app is None:
            raise DraftGeneratorError("Application not found.")
        return app

    def _build_profile_bundle(self) -> ProfileBundle:
        identity = self.session.execute(
            select(models.ProfileIdentity).limit(1)
        ).scalar_one_or_none()
        kit = self._load_kit()
        if identity is None:
            name = "Apoorva Chilukuri"
            preferred = None
            email = None
            phone = None
            base_location = None
            links: dict[str, str] = {}
            skills = sum(kit.profile.technical_strengths.values(), [])
            stack = STACK_DEFAULTS
        else:
            name = identity.name
            preferred = identity.preferred_name
            email = identity.email
            phone = identity.phone
            base_location = identity.base_location
            links = {}
            if identity.links:
                if identity.links.linkedin:
                    links["linkedin"] = identity.links.linkedin
                if identity.links.github:
                    links["github"] = identity.links.github
                if identity.links.portfolio:
                    links["portfolio"] = identity.links.portfolio
            skills = identity.facts.skills if identity.facts else []
            stack = identity.facts.tools if identity.facts else []

        projects: list[dict[str, str]] = []
        for project in self._load_kit().projects:
            projects.append(
                {
                    "name": project.name,
                    "one_liner": project.summary,
                    "metric": project.talking_points[0] if project.talking_points else "",
                }
            )

        return ProfileBundle(
            full_name=name,
            preferred_name=preferred,
            email=email,
            phone=phone,
            base_location=base_location,
            links=links,
            skills=skills,
            stack=stack,
            projects=projects,
        )

    def _fetch_feedback_history(self, application_id: UUID) -> list[FeedbackNote]:
        stmt: Select[tuple[models.ApplicationFeedback]] = (
            select(models.ApplicationFeedback)
            .where(models.ApplicationFeedback.application_id == application_id)
            .order_by(models.ApplicationFeedback.created_at)
        )
        rows = self.session.execute(stmt).scalars().all()
        return [FeedbackNote(role=entry.role.value, text=entry.text) for entry in rows]

    def _fetch_learning_notes(self) -> list[str]:
        kit = self._load_kit()
        stmt = (
            select(models.ApplicationFeedback.text)
            .where(models.ApplicationFeedback.role == FeedbackRole.SYSTEM)
            .order_by(models.ApplicationFeedback.created_at.desc())
            .limit(kit.learning.max_recent_notes)
        )
        rows = self.session.execute(stmt).scalars().all()
        return list(rows)

    def _persist_user_notes(
        self,
        application_id: UUID,
        notes: Sequence[str],
        author: str | None,
    ) -> None:
        now_notes = [note.strip() for note in notes if note and note.strip()]
        for note in now_notes:
            feedback = models.ApplicationFeedback(
                application_id=application_id,
                role=FeedbackRole.USER,
                author=author,
                text=note,
            )
            self.session.add(feedback)

    def _persist_assistant_note(
        self,
        application_id: UUID,
        text: str,
    ) -> None:
        feedback = models.ApplicationFeedback(
            application_id=application_id,
            role=FeedbackRole.ASSISTANT,
            text=text,
        )
        self.session.add(feedback)

    def _write_artifact(
        self,
        application: models.Application,
        version_number: int,
        letter: str,
    ) -> str:
        artifact_dir = ARTIFACTS_DIR / application.human_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        filename = f"cl-v{version_number}.md"
        path = artifact_dir / filename
        path.write_text(letter, encoding="utf-8")
        uri = f"file://{path.resolve()}"
        artifact = models.Artifact(
            application_id=application.id,
            type=ArtifactType.COVER_LETTER_VERSION,
            uri=uri,
        )
        self.session.add(artifact)
        return uri

    def _count_cover_letter_versions(self, application_id: UUID) -> int:
        stmt = select(func.count()).select_from(models.Artifact).where(
            models.Artifact.application_id == application_id,
            models.Artifact.type == ArtifactType.COVER_LETTER_VERSION,
        )
        return self.session.execute(stmt).scalar_one() or 0

    async def generate(
        self,
        application_id: UUID,
        *,
        notes: Sequence[str] | None = None,
        author: str | None = None,
        post_to_slack: bool = False,
        persist_notes: bool = True,
    ) -> DraftResult:
        application = self._ensure_application(application_id)
        job = application.job
        if job is None:
            raise DraftGeneratorError("Application missing job reference.")

        clean_notes = [n for n in (notes or []) if n]
        if clean_notes and persist_notes:
            self._persist_user_notes(application_id, clean_notes, author)

        context = DraftContext(
            application=application,
            job=job,
            profile=self._build_profile_bundle(),
            notes=list(clean_notes),
            feedback_history=self._fetch_feedback_history(application_id),
            learning_notes=self._fetch_learning_notes(),
        )

        payload = build_prompt_payload(context, self._load_kit())
        response: LlmResponse = await generate_cover_letter(payload)

        version_number = self._count_cover_letter_versions(application_id) + 1
        uri = self._write_artifact(application, version_number, response.cover_letter_md)
        self._persist_assistant_note(application_id, response.cover_letter_md)

        slack_channel_id = application.slack_channel_id
        slack_thread_ts = application.slack_thread_ts

        if application.status == ApplicationStatus.QUEUED:
            application.status = ApplicationStatus.DRAFTING

        self.session.commit()

        if post_to_slack:
            await self._post_to_thread(slack_channel_id, slack_thread_ts, response.cover_letter_md)

        return DraftResult(
            application_id=application.id,
            human_id=application.human_id,
            version=response.version or f"CL v{version_number}",
            cover_letter_md=response.cover_letter_md,
            artifact_uri=uri,
            payload=payload,
        )

    async def _post_to_thread(
        self,
        slack_channel_id: str | None,
        slack_thread_ts: str | None,
        letter: str,
    ) -> None:
        if not self.slack_client:
            return
        if not slack_channel_id or not slack_thread_ts:
            return
        try:
            await self.slack_client.post_thread_message(
                channel=slack_channel_id,
                thread_ts=slack_thread_ts,
                text=letter,
            )
        except Exception:
            pass

    async def finalize(
        self,
        application_id: UUID,
        *,
        author: str | None = None,
    ) -> str:
        application = self._ensure_application(application_id)
        application.status = ApplicationStatus.DRAFT_READY
        stmt = (
            select(models.ApplicationFeedback.text)
            .where(
                models.ApplicationFeedback.application_id == application_id,
                models.ApplicationFeedback.role == FeedbackRole.USER,
            )
            .order_by(models.ApplicationFeedback.created_at)
        )
        notes = self.session.execute(stmt).scalars().all()
        summary = await summarize_feedback(notes)
        learning_entry = models.ApplicationFeedback(
            application_id=application_id,
            role=FeedbackRole.SYSTEM,
            author=author,
            text=summary,
        )
        self.session.add(learning_entry)
        self.session.commit()
        if self.slack_client and application.slack_channel_id and application.slack_thread_ts:
            message = f"Marked `{application.human_id}` as Draft Ready.\nLearning note: {summary}"
            try:
                await self.slack_client.post_thread_message(
                    channel=application.slack_channel_id,
                    thread_ts=application.slack_thread_ts,
                    text=message,
                )
            except Exception:  # noqa: BLE001
                pass
        return summary


# Default stack for profiles without DB entries
STACK_DEFAULTS = ["Java", "Python", "SQL", "TypeScript/React", "REST APIs", "Docker"]
