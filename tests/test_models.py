from datetime import datetime, timezone
from uuid import uuid4

from agentic_jobs.core.enums import (
    ApplicationStatus,
    ArtifactType,
    JobSourceType,
    SubmissionMode,
    TrustVerdict,
)
from agentic_jobs.schemas import (
    ApplicationModel,
    ArtifactModel,
    JobModel,
    JobSourceModel,
    ProfileFactsModel,
    ProfileFilesModel,
    ProfileIdentityModel,
    ProfileLinksModel,
    ResumeVariantModel,
    TrustEventModel,
    WhitelistModel,
)


def _timestamp() -> datetime:
    return datetime.now(timezone.utc)


def test_job_model_validation() -> None:
    job = JobModel(
        id=uuid4(),
        title="Backend Software Engineer",
        company_name="Example Corp",
        location="New York, NY",
        url="https://jobs.example.com/backend-engineer",
        source_type=JobSourceType.GREENHOUSE,
        domain_root="example.com",
        submission_mode=SubmissionMode.ATS,
        jd_text="Responsibilities include building APIs.",
        requirements=[{"type": "text", "value": "Experience with FastAPI"}],
        job_id_canonical="GH:12345",
        scraped_at=_timestamp(),
        hash="abc123hash",
    )

    assert job.job_id_canonical == "GH:12345"


def test_job_source_model_validation() -> None:
    job_source = JobSourceModel(
        id=uuid4(),
        source_type=JobSourceType.GREENHOUSE,
        source_url="https://boards.greenhouse.io/example/jobs/12345",
        company_name="Example Corp",
        domain_root="greenhouse.io",
        raw_payload={"id": 12345, "title": "Backend Software Engineer"},
        discovered_at=_timestamp(),
        hash="sourcehash123",
    )

    assert job_source.raw_payload["id"] == 12345


def test_trust_event_model_validation() -> None:
    trust_event = TrustEventModel(
        id=uuid4(),
        domain_root="example.com",
        url="https://example.com",
        score=85,
        signals=[{"signal": "tls", "value": "hsts"}],
        verdict=TrustVerdict.AUTO_SAFE,
        created_at=_timestamp(),
    )

    assert trust_event.score == 85


def test_whitelist_model_validation() -> None:
    whitelist_entry = WhitelistModel(
        domain_root="example.com",
        company_name="Example Corp",
        ats_type="greenhouse",
        approved_by="admin",
        approved_at=_timestamp(),
    )

    assert whitelist_entry.company_name == "Example Corp"


def test_application_model_validation() -> None:
    application = ApplicationModel(
        id=uuid4(),
        human_id="APP-2024-001",
        job_id=uuid4(),
        status=ApplicationStatus.QUEUED,
        slack_channel_id="C1234567890",
        slack_thread_ts="1700000000.123456",
        score=92.5,
        canonical_job_id="GH:12345",
        submission_mode=SubmissionMode.ATS,
        created_at=_timestamp(),
        updated_at=_timestamp(),
    )

    assert application.status is ApplicationStatus.QUEUED


def test_artifact_model_validation() -> None:
    artifact = ArtifactModel(
        id=uuid4(),
        application_id=uuid4(),
        type=ArtifactType.JD_SNAPSHOT,
        uri="https://storage.example.com/jd_snapshot.pdf",
        created_at=_timestamp(),
    )

    assert artifact.type is ArtifactType.JD_SNAPSHOT


def test_profile_models_validation() -> None:
    identity = ProfileIdentityModel(
        id=uuid4(),
        name="Apoorva Chilukuri",
        preferred_name="Apoorva",
        email="apoorva@example.com",
        phone="+1-555-555-5555",
        base_location="San Francisco, CA",
    )

    links = ProfileLinksModel(
        id=uuid4(),
        identity_id=identity.id,
        linkedin="https://linkedin.com/in/apoorva",
        github="https://github.com/apoorvachilukuri",
        portfolio="https://apoorva.dev",
    )

    facts = ProfileFactsModel(
        id=uuid4(),
        identity_id=identity.id,
        skills=["Python", "SQL", "FastAPI"],
        tools=["Docker", "Git"],
        frameworks=["FastAPI", "SQLAlchemy"],
        projects=[
            {"name": "RAG Eval", "one_liner": "Evaluated RAG pipelines", "metric": "20% faster"}
        ],
        education="BS Computer Science",
        work_auth="US Citizen",
    )

    resume_variant = ResumeVariantModel(
        label="General",
        uri="https://storage.example.com/resume.pdf",
        created_at=_timestamp(),
    )

    files = ProfileFilesModel(
        id=uuid4(),
        identity_id=identity.id,
        resume_variants=[resume_variant],
    )

    assert identity.preferred_name == "Apoorva"
    assert links.identity_id == identity.id
    assert len(facts.projects) == 1
    assert files.resume_variants[0].label == "General"
