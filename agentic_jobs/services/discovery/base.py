from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

from agentic_jobs.core.enums import JobSourceType, SubmissionMode


class DiscoveryError(RuntimeError):
    """Raised when a discovery adapter encounters an unrecoverable error."""


class RobotsDisallowedError(DiscoveryError):
    """Raised when a fetch is disallowed by robots.txt."""


@dataclass(slots=True)
class JobRef:
    """Minimal representation of a job listing discovered from a source."""

    source: str
    org_slug: str
    job_id: str
    title: str
    location: str
    detail_url: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class JobDetail:
    """Full detail for a job listing, including raw HTML for normalization."""

    job_ref: JobRef
    html: str
    company_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DiscoverySummary:
    """Structured summary of a discovery run."""

    orgs_crawled: int = 0
    jobs_seen: int = 0
    jobs_inserted: int = 0
    domains_scored: int = 0


class SourceAdapter(Protocol):
    """Protocol for discovery adapters."""

    source_name: str
    job_source_type: JobSourceType
    submission_mode: SubmissionMode
    uses_frontier: bool = True

    async def discover(self) -> Sequence[str]:
        """Return a collection of organization slugs available from the source."""

    async def list_jobs(self, org_slug: str) -> Sequence[JobRef]:
        """Return job references for the provided organization slug."""

    async def fetch_job_detail(self, job_ref: JobRef) -> JobDetail:
        """Return detailed job content for the provided reference."""

    def canonical_id(self, job_ref: JobRef) -> str:
        """Return a canonical identifier for the job reference."""

    async def aclose(self) -> None:
        """Release any network resources."""
