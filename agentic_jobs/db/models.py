from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agentic_jobs.core.enums import (
    ApplicationStatus,
    ArtifactType,
    JobSourceType,
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
    domain_root: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    submission_mode: Mapped[SubmissionMode] = mapped_column(
        SAEnum(SubmissionMode, name="job_submission_mode", native_enum=False),
        nullable=False,
    )
    jd_text: Mapped[str] = mapped_column(Text, nullable=False)
    requirements: Mapped[List[dict]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    job_id_canonical: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
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
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    company_name: Mapped[Optional[str]] = mapped_column(String(255))
    domain_root: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
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
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
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
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
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
    slack_channel_id: Mapped[Optional[str]] = mapped_column(String(64))
    slack_thread_ts: Mapped[Optional[str]] = mapped_column(String(32))
    score: Mapped[Optional[float]] = mapped_column(Float)
    canonical_job_id: Mapped[str] = mapped_column(String(255), nullable=False)
    submission_mode: Mapped[SubmissionMode] = mapped_column(
        SAEnum(SubmissionMode, name="application_submission_mode", native_enum=False),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    job: Mapped[Job] = relationship(back_populates="applications")
    artifacts: Mapped[List["Artifact"]] = relationship(
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
        SAEnum(ArtifactType, name="artifact_type", native_enum=False), nullable=False
    )
    uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    application: Mapped[Application] = relationship(back_populates="artifacts")


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
