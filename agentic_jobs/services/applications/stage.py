from __future__ import annotations

from typing import Dict

from agentic_jobs.core.enums import ApplicationStage, ApplicationStatus
from agentic_jobs.db import models


STAGE_DISPLAY_NAMES: Dict[ApplicationStage, str] = {
    ApplicationStage.INTERESTED: "Interested",
    ApplicationStage.COVER_LETTER_IN_PROGRESS: "CL In Progress",
    ApplicationStage.COVER_LETTER_FINALIZED: "CL Finalized",
    ApplicationStage.SUBMITTED: "Submitted",
    ApplicationStage.INTERVIEWING: "Interviewing",
    ApplicationStage.ACCEPTED: "Accepted",
    ApplicationStage.REJECTED: "Rejected",
}


ARCHIVED_STAGES: set[ApplicationStage] = {
    ApplicationStage.ACCEPTED,
    ApplicationStage.REJECTED,
}


STAGE_STATUS_MAPPING: Dict[ApplicationStage, ApplicationStatus] = {
    ApplicationStage.INTERESTED: ApplicationStatus.QUEUED,
    ApplicationStage.COVER_LETTER_IN_PROGRESS: ApplicationStatus.DRAFTING,
    ApplicationStage.COVER_LETTER_FINALIZED: ApplicationStatus.DRAFT_READY,
    ApplicationStage.SUBMITTED: ApplicationStatus.SUBMITTED,
    ApplicationStage.INTERVIEWING: ApplicationStatus.APPROVED,
    ApplicationStage.ACCEPTED: ApplicationStatus.CLOSED,
    ApplicationStage.REJECTED: ApplicationStatus.REJECTED,
}


def stage_display(stage: ApplicationStage) -> str:
    return STAGE_DISPLAY_NAMES.get(stage, stage.value.replace("_", " ").title())


def apply_stage(application: models.Application, stage: ApplicationStage) -> None:
    application.stage = stage
    status = STAGE_STATUS_MAPPING.get(stage)
    if status:
        application.status = status


def is_archived_stage(stage: ApplicationStage) -> bool:
    return stage in ARCHIVED_STAGES
