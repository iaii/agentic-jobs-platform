from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import List, Optional

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agentic_jobs.core.enums import (
    ApplicationStage,
    ApplicationStatus,
    ArtifactType,
    AutofillMode,
    AutofillTaskStatus,
    DomainReviewStatus,
    FeedbackRole,
    JobSourceType,
    MemoryCategory,
    MemoryType,
    PipelineMode,
    PipelineStatus,
    SubmissionMode,
    TrustVerdict,
)
from agentic_jobs.db.session import Base


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_type: Mapped[JobSourceType] = mapped_column(
        SAEnum(JobSourceType, name="job_source_type", native_enum=False), nullable=False
    )
    source_name: Mapped[str] = mapped_column(String(128), nullable=False, default="unknown")
    domain_root: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    submission_mode: Mapped[SubmissionMode] = mapped_column(
        SAEnum(SubmissionMode, name="job_submission_mode", native_enum=False),
        nullable=False,
    )
    jd_text: Mapped[str] = mapped_column(Text, nullable=False)
    requirements: Mapped[List[dict]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    company_website: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    job_id_canonical: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)

    applications: Mapped[List["Application"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class JobSource(Base):
    __tablename__ = "job_sources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_type: Mapped[JobSourceType] = mapped_column(
        SAEnum(JobSourceType, name="job_source_type", native_enum=False), nullable=False
    )
    source_name: Mapped[str] = mapped_column(String(128), nullable=False, default="unknown")
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    company_name: Mapped[Optional[str]] = mapped_column(String(255))
    domain_root: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)


class FrontierOrg(Base):
    __tablename__ = "frontier_orgs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    org_slug: Mapped[str] = mapped_column(String(255), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    last_crawled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    muted_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("source", "org_slug", name="uq_frontier_org_source_slug"),
    )


class TrustEvent(Base):
    __tablename__ = "trust_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    domain_root: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    signals: Mapped[List[dict]] = mapped_column(JSONB, nullable=False, default=list)
    verdict: Mapped[TrustVerdict] = mapped_column(
        SAEnum(TrustVerdict, name="trust_verdict", native_enum=False), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class Whitelist(Base):
    __tablename__ = "whitelist"

    domain_root: Mapped[str] = mapped_column(String(255), primary_key=True)
    company_name: Mapped[Optional[str]] = mapped_column(String(255))
    ats_type: Mapped[Optional[str]] = mapped_column(String(64))
    approved_by: Mapped[Optional[str]] = mapped_column(String(255))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    human_id: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id"), nullable=False
    )
    status: Mapped[ApplicationStatus] = mapped_column(
        SAEnum(ApplicationStatus, name="application_status", native_enum=False),
        nullable=False,
    )
    stage: Mapped[ApplicationStage] = mapped_column(
        SAEnum(
            ApplicationStage,
            name="application_stage",
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=ApplicationStage.INTERESTED,
    )
    slack_channel_id: Mapped[Optional[str]] = mapped_column(String(64))
    slack_thread_ts: Mapped[Optional[str]] = mapped_column(String(32))
    score: Mapped[Optional[float]] = mapped_column(Float)
    canonical_job_id: Mapped[str] = mapped_column(String(255), nullable=False)
    submission_mode: Mapped[SubmissionMode] = mapped_column(
        SAEnum(SubmissionMode, name="application_submission_mode", native_enum=False),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    job: Mapped[Job] = relationship(back_populates="applications")
    artifacts: Mapped[List["Artifact"]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )
    feedback: Mapped[List["ApplicationFeedback"]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )
    autofill_tasks: Mapped[List["AutofillTask"]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("canonical_job_id", name="uq_applications_canonical_job_id"),
    )


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("applications.id"), nullable=False
    )
    type: Mapped[ArtifactType] = mapped_column(
        SAEnum(ArtifactType, name="artifact_type", native_enum=False, length=64), nullable=False
    )
    uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    application: Mapped[Application] = relationship(back_populates="artifacts")


class ApplicationFeedback(Base):
    __tablename__ = "application_feedback"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("applications.id"), nullable=False, index=True
    )
    role: Mapped[FeedbackRole] = mapped_column(
        SAEnum(FeedbackRole, name="feedback_role", native_enum=False), nullable=False
    )
    author: Mapped[Optional[str]] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    application: Mapped[Application] = relationship(back_populates="feedback")


class AutofillTask(Base):
    __tablename__ = "autofill_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("applications.id"), nullable=False, index=True
    )
    status: Mapped[AutofillTaskStatus] = mapped_column(
        SAEnum(AutofillTaskStatus, name="autofill_task_status", native_enum=False),
        nullable=False,
    )
    mode: Mapped[AutofillMode] = mapped_column(
        SAEnum(AutofillMode, name="autofill_mode", native_enum=False), nullable=False
    )
    domain_root: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    resume_path: Mapped[Optional[str]] = mapped_column(String(1024))
    cover_letter_path: Mapped[Optional[str]] = mapped_column(String(1024))
    final_url: Mapped[Optional[str]] = mapped_column(String(1024))
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    payload_metadata: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    application: Mapped[Application] = relationship(back_populates="autofill_tasks")


class DigestLog(Base):
    __tablename__ = "digest_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id"), nullable=False
    )
    digest_date: Mapped[date] = mapped_column(Date, nullable=False)
    slack_channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    slack_message_ts: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    job: Mapped[Job] = relationship("Job")

    __table_args__ = (
        UniqueConstraint("job_id", "digest_date", name="uq_digest_job_date"),
    )


class DomainReview(Base):
    __tablename__ = "domain_reviews"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    domain_root: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    status: Mapped[DomainReviewStatus] = mapped_column(
        SAEnum(DomainReviewStatus, name="domain_review_status", native_enum=False),
        nullable=False,
        default=DomainReviewStatus.PENDING,
    )
    slack_channel_id: Mapped[Optional[str]] = mapped_column(String(64))
    slack_message_ts: Mapped[Optional[str]] = mapped_column(String(32))
    company_name: Mapped[Optional[str]] = mapped_column(String(255))
    ats_type: Mapped[Optional[str]] = mapped_column(String(64))
    muted_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ProfileIdentity(Base):
    __tablename__ = "profile_identities"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    preferred_name: Mapped[Optional[str]] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    phone: Mapped[str] = mapped_column(String(50), nullable=False)
    base_location: Mapped[str] = mapped_column(String(255), nullable=False)

    links: Mapped["ProfileLinks"] = relationship(
        "ProfileLinks", back_populates="identity", uselist=False, cascade="all, delete-orphan"
    )
    facts: Mapped["ProfileFacts"] = relationship(
        "ProfileFacts", back_populates="identity", uselist=False, cascade="all, delete-orphan"
    )
    files: Mapped["ProfileFiles"] = relationship(
        "ProfileFiles", back_populates="identity", uselist=False, cascade="all, delete-orphan"
    )


class ProfileLinks(Base):
    __tablename__ = "profile_links"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profile_identities.id"), nullable=False, unique=True
    )
    linkedin: Mapped[Optional[str]] = mapped_column(String(1024))
    github: Mapped[Optional[str]] = mapped_column(String(1024))
    portfolio: Mapped[Optional[str]] = mapped_column(String(1024))

    identity: Mapped[ProfileIdentity] = relationship(back_populates="links")


class ProfileFacts(Base):
    __tablename__ = "profile_facts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profile_identities.id"), nullable=False, unique=True
    )
    skills: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    tools: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    frameworks: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    projects: Mapped[List[dict]] = mapped_column(JSONB, nullable=False, default=list)
    education: Mapped[Optional[str]] = mapped_column(String(1024))
    work_auth: Mapped[Optional[str]] = mapped_column(String(255))

    identity: Mapped[ProfileIdentity] = relationship(back_populates="facts")


class ProfileFiles(Base):
    __tablename__ = "profile_files"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profile_identities.id"), nullable=False, unique=True
    )
    resume_variants: Mapped[List[dict]] = mapped_column(JSONB, nullable=False, default=list)

    identity: Mapped[ProfileIdentity] = relationship(back_populates="files")


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("applications.id"), nullable=False, index=True
    )
    mode: Mapped[PipelineMode] = mapped_column(
        SAEnum(PipelineMode, name="pipeline_mode", native_enum=False), nullable=False
    )
    status: Mapped[PipelineStatus] = mapped_column(
        SAEnum(PipelineStatus, name="pipeline_status", native_enum=False),
        nullable=False,
        default=PipelineStatus.RUNNING,
    )
    agent_log: Mapped[List[dict]] = mapped_column(JSONB, nullable=False, default=list)
    final_score: Mapped[Optional[float]] = mapped_column(Float)
    revision_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    application: Mapped["Application"] = relationship("Application")


class AgentMemory(Base):
    __tablename__ = "agent_memories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    application_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("applications.id"), nullable=True, index=True
    )
    memory_type: Mapped[MemoryType] = mapped_column(
        SAEnum(MemoryType, name="memory_type", native_enum=False), nullable=False
    )
    category: Mapped[MemoryCategory] = mapped_column(
        SAEnum(MemoryCategory, name="memory_category", native_enum=False), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class VaultEmbedding(Base):
    __tablename__ = "vault_embeddings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    heading: Mapped[str] = mapped_column(String(512), nullable=False)
    section_text: Mapped[str] = mapped_column(Text, nullable=False)
    wikilinks: Mapped[List[str]] = mapped_column(JSONB, nullable=False, default=list)
    embedding: Mapped[Optional[List[float]]] = mapped_column(JSONB, nullable=True)
    file_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("file_path", "heading", name="uq_vault_embedding_file_heading"),
    )


class CompanyCache(Base):
    __tablename__ = "company_cache"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    domain: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    scraped_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    ttl_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=168)


class TrackerView(Base):
    __tablename__ = "tracker_views"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    view_type: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    slack_channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    slack_message_ts: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
