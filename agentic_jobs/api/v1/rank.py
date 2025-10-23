from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from uuid import UUID

from agentic_jobs.db import models
from agentic_jobs.db.session import get_session
from agentic_jobs.services.ranking import score_job


router = APIRouter()


@router.get("/sample")
def get_rank_sample(job_id: UUID = Query(...), db: Session = Depends(get_session)):
    job = db.get(models.Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    result = score_job(job)
    return {
        "score": result.score,
        "rationale": result.rationale,
        "features": result.features,
    }


