from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agentic_jobs.db import models

LOGGER = logging.getLogger(__name__)


def next_human_id(session: Session) -> str:
    """Return the next sequential ``APP-<year>-<seq>`` human id.

    The sequence is computed numerically rather than by lexicographic string
    ordering. String ordering breaks once the counter reaches ``APP-<year>-1000``
    because ``"APP-2026-1000" < "APP-2026-999"`` as strings — the old query would
    keep returning ``...-999`` and stall (and collide) at the 1000th application
    of the year. We scan the current year's ids and take the numeric max instead.

    The ``:03d`` zero-pad is preserved so ids 1–999 render identically to before;
    ids 1000+ simply widen naturally.
    """
    now = datetime.now(tz=timezone.utc)
    prefix = f"APP-{now.year}-"
    rows = (
        session.execute(
            select(models.Application.human_id).where(
                models.Application.human_id.like(f"{prefix}%")
            )
        )
        .scalars()
        .all()
    )

    max_seq = 0
    for human_id in rows:
        suffix = human_id.rsplit("-", 1)[-1]
        try:
            seq = int(suffix)
        except ValueError as exc:
            raise RuntimeError(f"Corrupt human_id in database: {human_id!r}") from exc
        max_seq = max(max_seq, seq)

    return f"{prefix}{max_seq + 1:03d}"


def insert_application_with_human_id(
    session: Session,
    build: Callable[[str], models.Application],
    *,
    max_attempts: int = 5,
) -> models.Application:
    """Flush a new ``Application``, retrying on ``human_id`` uniqueness collisions.

    ``build(human_id)`` must return a fresh, unsaved ``Application`` constructed
    with the supplied id. ``next_human_id`` reads the current max and returns
    max+1, so two concurrent creates can compute the same id between that SELECT
    and the INSERT (TOCTOU). When the unique constraint rejects the duplicate we
    roll back and recompute. Re-raises ``IntegrityError`` if every attempt
    collides so callers can surface a meaningful error.
    """
    for attempt in range(max_attempts):
        human_id = next_human_id(session)
        app = build(human_id)
        session.add(app)
        try:
            session.flush()
            return app
        except IntegrityError:
            session.rollback()
            if attempt == max_attempts - 1:
                raise
            LOGGER.warning(
                "human_id collision on %s, retrying (%d/%d)",
                human_id, attempt + 1, max_attempts,
            )
    raise AssertionError("unreachable: loop returns or raises")
