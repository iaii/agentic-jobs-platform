from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agentic_jobs.core.enums import ApplicationStage, ArtifactType
from agentic_jobs.db import models
from agentic_jobs.db.session import get_session
from agentic_jobs.services.applications.stage import apply_stage
from agentic_jobs.services.artifacts.utils import ARTIFACTS_DIR
from agentic_jobs.services.ranking import score_job

router = APIRouter()


class CreateApplicationRequest(BaseModel):
    job_id: uuid.UUID


class CreateApplicationResponse(BaseModel):
    application_id: str
    human_id: str
    job_id: str
    stage: str
    status: str
    score: float | None
    created_at: datetime


def _next_human_id(session: Session) -> str:
    now = datetime.now(tz=timezone.utc)
    prefix = f"APP-{now.year}-"
    stmt = (
        select(models.Application.human_id)
        .where(models.Application.human_id.like(f"{prefix}%"))
        .order_by(models.Application.human_id.desc())
        .limit(1)
    )
    last_id = session.execute(stmt).scalar_one_or_none()
    if last_id:
        try:
            next_seq = int(last_id.split("-")[-1]) + 1
        except ValueError as exc:
            raise RuntimeError(f"Corrupt human_id in database: {last_id!r}") from exc
    else:
        next_seq = 1
    return f"{prefix}{next_seq:03d}"


def _persist_jd_snapshot(session: Session, application: models.Application, job: models.Job) -> None:
    if not job.jd_text:
        return
    existing = session.execute(
        select(models.Artifact.id)
        .where(
            models.Artifact.application_id == application.id,
            models.Artifact.type == ArtifactType.JD_SNAPSHOT,
        )
        .limit(1)
    ).scalar_one_or_none()
    if existing:
        return
    artifact_dir = ARTIFACTS_DIR / application.human_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    jd_path = artifact_dir / "jd.md"
    jd_path.write_text(job.jd_text, encoding="utf-8")
    session.add(models.Artifact(
        application_id=application.id,
        type=ArtifactType.JD_SNAPSHOT,
        uri=f"file://{jd_path.resolve()}",
    ))


@router.post(
    "/create",
    status_code=status.HTTP_201_CREATED,
    response_model=CreateApplicationResponse,
)
async def create_application(
    body: CreateApplicationRequest,
    db: Session = Depends(get_session),
) -> CreateApplicationResponse:
    job = db.get(models.Job, body.job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    existing = db.execute(
        select(models.Application).where(
            models.Application.canonical_job_id == job.job_id_canonical
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Already tracked as {existing.human_id}",
        )

    score_result = score_job(job)
    app = models.Application(
        human_id=_next_human_id(db),
        job_id=job.id,
        score=score_result.score,
        canonical_job_id=job.job_id_canonical,
        submission_mode=job.submission_mode,
    )
    apply_stage(app, ApplicationStage.INTERESTED)
    db.add(app)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        # Two concurrent creates raced on human_id — recompute and retry once.
        app.human_id = _next_human_id(db)
        db.add(app)
        db.flush()
    _persist_jd_snapshot(db, app, job)
    db.commit()
    db.refresh(app)

    return CreateApplicationResponse(
        application_id=str(app.id),
        human_id=app.human_id,
        job_id=str(job.id),
        stage=app.stage.value,
        status=app.status.value,
        score=app.score,
        created_at=app.created_at,
    )
