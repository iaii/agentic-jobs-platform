from __future__ import annotations

from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_jobs.core.enums import ArtifactType
from agentic_jobs.db import models


# Absolute path anchored to the project root (three levels up from this file).
# This prevents breakage when the process working directory is not the repo root.
ARTIFACTS_DIR = Path(__file__).resolve().parents[3] / "artifacts"


def ensure_artifact_dir(human_id: str) -> Path:
    path = ARTIFACTS_DIR / human_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_artifact_path(
    session: Session,
    application_id: UUID,
    artifact_type: ArtifactType,
    *,
    latest: bool = True,
) -> Path | None:
    stmt = (
        select(models.Artifact)
        .where(
            models.Artifact.application_id == application_id,
            models.Artifact.type == artifact_type,
        )
        .order_by(models.Artifact.created_at.desc() if latest else models.Artifact.created_at)
        .limit(1)
    )
    artifact = session.execute(stmt).scalar_one_or_none()
    if not artifact:
        return None
    return artifact_uri_to_path(artifact.uri)


def load_artifact_text(
    session: Session,
    application_id: UUID,
    artifact_type: ArtifactType,
    *,
    latest: bool = True,
) -> Optional[str]:
    path = get_artifact_path(session, application_id, artifact_type, latest=latest)
    if not path or not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def artifact_uri_to_path(uri: str | None) -> Path | None:
    if not uri:
        return None
    parsed = urlparse(uri)
    if parsed.scheme and parsed.scheme != "file":
        return None
    path = Path(unquote(parsed.path))
    # Guard against path traversal: resolved path must be inside ARTIFACTS_DIR
    try:
        resolved = path.resolve()
        artifacts_resolved = ARTIFACTS_DIR.resolve()
        resolved.relative_to(artifacts_resolved)
    except ValueError:
        return None
    return path
