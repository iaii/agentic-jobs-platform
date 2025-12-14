from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl

from agentic_jobs.core.enums import (
    ApplicationStatus,
    ArtifactType,
    FeedbackRole,
    JobSourceType,
    SubmissionMode,
    TrustVerdict,
)


class JobModel(BaseModel):
    id: UUID
    title: str
    company_name: str
    location: str
    url: HttpUrl
    source_type: JobSourceType
    domain_root: str
    submission_mode: SubmissionMode
    jd_text: str
    requirements: List[dict[str, Any]] = Field(default_factory=list)
    job_id_canonical: str
    scraped_at: datetime
    hash: str


class JobSourceModel(BaseModel):
    id: UUID
    source_type: JobSourceType
    source_url: HttpUrl
    company_name: Optional[str] = None
    domain_root: str
    raw_payload: dict[str, Any]
    discovered_at: datetime
    hash: str


class TrustEventModel(BaseModel):
    id: UUID
    domain_root: str
    url: HttpUrl
    score: int
    signals: List[dict[str, Any]] = Field(default_factory=list)
    verdict: TrustVerdict
    created_at: datetime


class WhitelistModel(BaseModel):
    domain_root: str
    company_name: Optional[str] = None
    ats_type: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None


class ApplicationModel(BaseModel):
    id: UUID
    human_id: str
    job_id: UUID
    status: ApplicationStatus
    slack_channel_id: Optional[str] = None
    slack_thread_ts: Optional[str] = None
    score: Optional[float] = None
    canonical_job_id: str
    submission_mode: SubmissionMode
    created_at: datetime
    updated_at: datetime


class ArtifactModel(BaseModel):
    id: UUID
    application_id: UUID
    type: ArtifactType
    uri: HttpUrl
    created_at: datetime


class ApplicationFeedbackModel(BaseModel):
    id: UUID
    application_id: UUID
    role: FeedbackRole
    author: Optional[str] = None
    text: str
    created_at: datetime


class ProfileIdentityModel(BaseModel):
    id: UUID
    name: str
    preferred_name: Optional[str] = None
    email: str
    phone: str
    base_location: str


class ProfileLinksModel(BaseModel):
    id: UUID
    identity_id: UUID
    linkedin: Optional[HttpUrl] = None
    github: Optional[HttpUrl] = None
    portfolio: Optional[HttpUrl] = None


class ProjectFactModel(BaseModel):
    name: str
    one_liner: str
    metric: str


class ProfileFactsModel(BaseModel):
    id: UUID
    identity_id: UUID
    skills: List[str] = Field(default_factory=list)
    tools: List[str] = Field(default_factory=list)
    frameworks: List[str] = Field(default_factory=list)
    projects: List[ProjectFactModel] = Field(default_factory=list)
    education: Optional[str] = None
    work_auth: Optional[str] = None


class ResumeVariantModel(BaseModel):
    label: str
    uri: HttpUrl
    created_at: datetime


class ProfileFilesModel(BaseModel):
    id: UUID
    identity_id: UUID
    resume_variants: List[ResumeVariantModel] = Field(default_factory=list)
