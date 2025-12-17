from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:
        return str(self.value)


class JobSourceType(StrEnum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    COMPANY = "company"


class SubmissionMode(StrEnum):
    ATS = "ats"
    DEEPLINK = "deeplink"


class TrustVerdict(StrEnum):
    AUTO_SAFE = "auto-safe"
    NEEDS_HUMAN_APPROVAL = "needs-human-approval"
    REJECT = "reject"


class ApplicationStatus(StrEnum):
    QUEUED = "Queued"
    DRAFTING = "Drafting"
    DRAFT_READY = "Draft Ready"
    APPROVED = "Approved"
    SUBMITTED = "Submitted"
    REJECTED = "Rejected"
    CLOSED = "Closed"


class ApplicationStage(StrEnum):
    INTERESTED = "interested"
    COVER_LETTER_IN_PROGRESS = "cover_letter_in_progress"
    COVER_LETTER_FINALIZED = "cover_letter_finalized"
    SUBMITTED = "submitted"
    INTERVIEWING = "interviewing"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ArtifactType(StrEnum):
    JD_SNAPSHOT = "jd_snapshot"
    COVER_LETTER_VERSION = "cover_letter_vN"
    AUTOFILL_SUMMARY = "autofill_summary"
    CONFIRMATION = "confirmation"


class FeedbackRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class DomainReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    MUTED = "muted"
