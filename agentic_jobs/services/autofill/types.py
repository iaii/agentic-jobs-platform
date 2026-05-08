from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from agentic_jobs.core.enums import AutofillMode, AutofillTaskStatus


@dataclass(slots=True)
class AutofillQueueResult:
    application_id: UUID
    human_id: str
    status: AutofillTaskStatus
    mode: AutofillMode
    message: str
    summary_path: Path | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime | None = None
    task_id: UUID | None = None


@dataclass(slots=True)
class AutofillStatusUpdate:
    human_id: str
    status: AutofillTaskStatus
    message: str | None = None
    final_url: str | None = None
    blocked_reason: str | None = None
    screenshot_path: str | None = None
    metadata: dict[str, Any] | None = None
