from __future__ import annotations

import json
import logging
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import subprocess
import sys

from sqlalchemy.orm import Session

from agentic_jobs.config import Settings, settings
from agentic_jobs.core.enums import ArtifactType, AutofillMode, AutofillTaskStatus
from agentic_jobs.db import models
from agentic_jobs.services.artifacts.utils import ensure_artifact_dir, load_artifact_text
from agentic_jobs.services.autofill.notifications import post_ops_update
from agentic_jobs.services.autofill.pdf import render_cover_letter_pdf
from agentic_jobs.services.autofill.profile import AutofillProfile, ProfileLoadError, ProfileLoader
from agentic_jobs.services.autofill.status import process_status_update
from agentic_jobs.services.autofill.types import AutofillQueueResult, AutofillStatusUpdate
from agentic_jobs.services.slack.client import SlackClient
from agentic_jobs.services.trust.whitelist import lookup_auto_whitelist


LOGGER = logging.getLogger(__name__)


class AutofillError(RuntimeError):
    """Raised when autofill orchestration cannot proceed."""


class AutofillOrchestrator:
    def __init__(self, config: Settings | None = None) -> None:
        self.settings = config or settings
        self.profile_loader = ProfileLoader(self.settings)

    @property
    def enabled(self) -> bool:
        return bool(self.settings.autofill_enabled)

    async def queue_application(
        self,
        session: Session,
        application: models.Application,
        slack_client: SlackClient,
        *,
        mode: AutofillMode,
        actor: str | None = None,
        auto_start: bool = True,
    ) -> AutofillQueueResult:
        if not self.enabled:
            raise AutofillError("Autofill is disabled in configuration.")
        job = application.job
        if job is None:
            raise AutofillError("Application missing job reference.")
        # Capture scalar values now — session.commit() calls later will expire the ORM
        # objects and accessing lazy attributes on detached instances raises an error.
        domain_root: str = job.domain_root or ""

        allowed = self._is_domain_allowed(session, domain_root)
        if not allowed:
            message = f"Domain `{job.domain_root}` is not allowed for autofill."
            await post_ops_update(
                slack_client,
                text=f"Autofill skipped for `{application.human_id}` — domain not allowed.",
            )
            return AutofillQueueResult(
                application_id=application.id,
                human_id=application.human_id,
                status=AutofillTaskStatus.SKIPPED,
                mode=mode,
                message=message,
                metadata={"domain_root": domain_root},
            )

        try:
            profile = self.profile_loader.load(session)
        except ProfileLoadError as exc:
            LOGGER.warning("Autofill profile unavailable: %s", exc)
            await post_ops_update(
                slack_client,
                text=f"Autofill blocked for `{application.human_id}` — profile missing.",
            )
            return AutofillQueueResult(
                application_id=application.id,
                human_id=application.human_id,
                status=AutofillTaskStatus.FAILED,
                mode=mode,
                message=str(exc),
            )

        cover_letter_text = load_artifact_text(session, application.id, ArtifactType.COVER_LETTER_VERSION)
        artifact_dir = ensure_artifact_dir(application.human_id)
        cover_letter_pdf: Path | None = None
        if (
            cover_letter_text
            and self.settings.autofill_cl_pdf_enabled
            and profile.files.cover_letter_pdf_enabled
        ):
            target_path = profile.files.cover_letter_pdf_path or (artifact_dir / "cover-letter.pdf")
            try:
                cover_letter_pdf = render_cover_letter_pdf(cover_letter_text, Path(target_path))
            except Exception:  # noqa: BLE001
                LOGGER.exception("Failed to render cover-letter PDF for %s", application.human_id)

        resume_path = profile.files.select_resume()

        summary_entry = self._build_summary_entry(
            application=application,
            job=job,
            profile=profile,
            resume_path=resume_path,
            cover_letter_pdf=cover_letter_pdf,
            cover_letter_text=cover_letter_text,
            mode=mode,
            actor=actor,
        )
        summary_path = self._write_summary_file(artifact_dir, summary_entry)
        self._record_artifact(session, application, summary_path)
        task = self._create_task_record(
            session,
            application,
            mode=mode,
            domain_root=domain_root,
            summary_path=summary_path,
            resume_path=resume_path,
            cover_letter_path=cover_letter_pdf,
        )
        task_id = task.id  # capture before session state changes

        if auto_start:
            await self._start_task(session, application, task, slack_client)

        now = datetime.now(tz=timezone.utc)
        status = AutofillTaskStatus.IN_PROGRESS if auto_start else AutofillTaskStatus.QUEUED
        message = "Autofill started." if auto_start else "Queued for autofill."
        return AutofillQueueResult(
            application_id=application.id,
            human_id=application.human_id,
            status=status,
            mode=mode,
            message=message,
            summary_path=summary_path,
            metadata={"domain_root": domain_root, "task_id": str(task_id)},
            created_at=now,
            task_id=task_id,
        )

    def _is_domain_allowed(self, session: Session, domain_root: str | None) -> bool:
        if not domain_root:
            return False
        domain = domain_root.lower()
        if domain in (self.settings.autofill_allowed_domains_list or []):
            return True
        if lookup_auto_whitelist(domain):
            return True
        if session.get(models.Whitelist, domain):
            return True
        return False

    def _open_job_tab(self, url: str | None, human_id: str | None = None) -> None:
        if not url:
            return
        try:
            launch_url = self._with_autofill_marker(url, human_id)
            if sys.platform == "darwin":
                subprocess.Popen(["open", "-g", launch_url])
            else:
                webbrowser.open(launch_url, new=2)
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to open browser for %s", url)

    def _with_autofill_marker(self, url: str, human_id: str | None) -> str:
        if not human_id:
            return url
        parts = urlsplit(url)
        fragment = parts.fragment or ""
        if "ajp_autofill=" in fragment:
            return url
        marker = f"ajp_autofill={human_id}"
        new_fragment = f"{fragment}&{marker}" if fragment else marker
        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, new_fragment))

    def _build_summary_entry(
        self,
        *,
        application: models.Application,
        job: models.Job,
        profile: AutofillProfile,
        resume_path: Path | None,
        cover_letter_pdf: Path | None,
        cover_letter_text: str | None,
        mode: AutofillMode,
        actor: str | None,
    ) -> dict:
        now = datetime.now(tz=timezone.utc)
        address = profile.identity.address
        sanitized_identity = {
            "full_name": profile.identity.full_name,
            "preferred_name": profile.identity.preferred_name,
            "email": profile.identity.email,
            "phone": profile.identity.phone,
            "base_location": profile.identity.base_location,
            "address": {
                "line1": address.line1 if address else None,
                "line2": address.line2 if address else None,
                "city": address.city if address else None,
                "state": address.state if address else None,
                "postal_code": address.postal_code if address else None,
                "country": address.country if address else None,
            }
            if address
            else None,
        }
        return {
            "application_id": str(application.id),
            "human_id": application.human_id,
            "identity": sanitized_identity,
            "job": {
                "title": job.title,
                "company": job.company_name,
                "location": job.location,
                "url": job.url,
                "domain_root": job.domain_root,
            },
            "mode": mode.value,
            "resume_path": str(resume_path) if resume_path else None,
            "cover_letter_pdf": str(cover_letter_pdf) if cover_letter_pdf else None,
            "cover_letter_text": cover_letter_text or None,
            "links": profile.links,
            "facts": profile.facts,
            "compliance": profile.compliance,
            "timestamp": now.isoformat(),
            "actor": actor,
        }

    def _write_summary_file(self, artifact_dir: Path, entry: dict) -> Path:
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
        summary_path = artifact_dir / f"autofill-summary-{timestamp}.json"
        summary_path.write_text(json.dumps(entry, indent=2), encoding="utf-8")
        return summary_path

    def _record_artifact(
        self,
        session: Session,
        application: models.Application,
        summary_path: Path,
    ) -> None:
        artifact = models.Artifact(
            application_id=application.id,
            type=ArtifactType.AUTOFILL_SUMMARY,
            uri=f"file://{summary_path.resolve()}",
        )
        session.add(artifact)
        session.commit()

    def _create_task_record(
        self,
        session: Session,
        application: models.Application,
        *,
        mode: AutofillMode,
        domain_root: str,
        summary_path: Path,
        resume_path: Path | None,
        cover_letter_path: Path | None,
    ) -> models.AutofillTask:
        task = models.AutofillTask(
            application_id=application.id,
            status=AutofillTaskStatus.QUEUED,
            mode=mode,
            domain_root=domain_root,
            payload_path=str(summary_path),
            resume_path=str(resume_path) if resume_path else None,
            cover_letter_path=str(cover_letter_path) if cover_letter_path else None,
        )
        session.add(task)
        session.commit()
        return task

    async def run_pending_task(
        self,
        session: Session,
        task: models.AutofillTask,
        slack_client: SlackClient,
    ) -> bool:
        if task.status is not AutofillTaskStatus.QUEUED:
            return False
        application = session.get(models.Application, task.application_id)
        if not application:
            return False
        await self._start_task(session, application, task, slack_client)
        return True

    async def _start_task(
        self,
        session: Session,
        application: models.Application,
        task: models.AutofillTask,
        slack_client: SlackClient,
    ) -> None:
        summary = self._load_summary_payload(task.payload_path)
        job_url = (summary.get("job") or {}).get("url")
        self._open_job_tab(job_url, application.human_id)
        await process_status_update(
            session,
            application,
            AutofillStatusUpdate(
                human_id=application.human_id,
                status=AutofillTaskStatus.IN_PROGRESS,
                message="Autofill launched.",
            ),
            slack_client,
        )

    def _load_summary_payload(self, summary_path: str | Path) -> dict:
        path = Path(summary_path)
        if not path.exists():
            raise AutofillError(f"Autofill summary missing at {path}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise AutofillError(f"Malformed autofill summary {path}") from exc
