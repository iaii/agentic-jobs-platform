from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from agentic_jobs.config import settings
from agentic_jobs.core.enums import ApplicationStage
from agentic_jobs.db import models
from agentic_jobs.services.applications.stage import (
    ARCHIVED_STAGES,
    stage_display,
)
from agentic_jobs.services.slack.client import SlackClient, SlackError


ROWS_PER_PAGE = 25
MAX_TRACKER_PAGES = 4
MAX_TRACKER_ROWS = ROWS_PER_PAGE * MAX_TRACKER_PAGES
TRACKER_VIEW_TYPE = "applications_master"
STAGE_SUMMARY_ORDER: list[ApplicationStage] = [
    ApplicationStage.INTERESTED,
    ApplicationStage.COVER_LETTER_IN_PROGRESS,
    ApplicationStage.COVER_LETTER_FINALIZED,
    ApplicationStage.SUBMITTED,
    ApplicationStage.INTERVIEWING,
]


@dataclass(slots=True)
class TrackerRow:
    application_id: UUID
    human_id: str
    stage: ApplicationStage
    score: float | None
    updated_at: datetime
    job_title: str
    company: str
    location: str
    url: str


class MasterTracker:
    def __init__(self, session: Session, slack_client: SlackClient) -> None:
        self.session = session
        self.slack_client = slack_client

    async def refresh(self) -> None:
        channel_id = settings.slack_jobs_tracker_channel
        if not channel_id:
            return

        self.session.flush()

        rows = self._load_rows()
        stage_counts = self._count_active_stages()
        total_active = sum(stage_counts.values())
        pages = self._chunk_rows(rows)
        if not pages:
            pages = [[]]
        total_pages = len(pages)

        existing_views = self._get_views()
        used_keys: set[str] = set()
        now = datetime.now(tz=timezone.utc)

        for index, page_rows in enumerate(pages, start=1):
            view_key = self._view_type_for_page(index)
            blocks = self._build_blocks(page_rows, stage_counts, total_active, index, total_pages)
            view = existing_views.get(view_key)
            if not view or not view.slack_message_ts:
                response = await self.slack_client.post_message(
                    channel=channel_id,
                    text="Master Job Tracker",
                    blocks=blocks,
                )
                new_channel = response.data.get("channel") or channel_id
                new_ts = response.data.get("ts") or response.data.get("message", {}).get("ts")
                if not new_ts:
                    continue
                if view is None:
                    view = models.TrackerView(
                        view_type=view_key,
                        slack_channel_id=new_channel,
                        slack_message_ts=new_ts,
                    )
                    self.session.add(view)
                else:
                    view.slack_channel_id = new_channel
                    view.slack_message_ts = new_ts
            else:
                await self.slack_client.update_message(
                    channel=view.slack_channel_id,
                    ts=view.slack_message_ts,
                    text="Master Job Tracker",
                    blocks=blocks,
                )
            view.view_type = view_key
            view.updated_at = now
            used_keys.add(view_key)

        for key, view in existing_views.items():
            if key in used_keys:
                continue
            try:
                await self.slack_client.delete_message(view.slack_channel_id, view.slack_message_ts)
            except SlackError:
                LOGGER.debug("Failed to delete stale tracker page %s", key)
            self.session.delete(view)

        self.session.commit()

    def _load_rows(self) -> list[TrackerRow]:
        archived_values = [stage.value for stage in ARCHIVED_STAGES]
        stmt = (
            select(models.Application)
            .options(joinedload(models.Application.job))
            .where(models.Application.stage.notin_(archived_values))
            .order_by(models.Application.updated_at.desc())
            .limit(MAX_TRACKER_ROWS)
        )
        apps = self.session.execute(stmt).scalars().all()
        rows: list[TrackerRow] = []
        for app in apps:
            job = app.job
            if not job:
                continue
            rows.append(
                TrackerRow(
                    application_id=app.id,
                    human_id=app.human_id,
                    stage=app.stage,
                    score=app.score,
                    updated_at=app.updated_at,
                    job_title=job.title,
                    company=job.company_name,
                    location=job.location,
                    url=job.url,
                )
            )
        return rows

    def _count_active_stages(self) -> Counter[str]:
        archived_values = [stage.value for stage in ARCHIVED_STAGES]
        stmt = (
            select(models.Application.stage, func.count())
            .where(models.Application.stage.notin_(archived_values))
            .group_by(models.Application.stage)
        )
        counts: Counter[str] = Counter()
        for stage_value, count in self.session.execute(stmt):
            counts[stage_value] = count
        return counts

    def _get_views(self) -> dict[str, models.TrackerView]:
        stmt = select(models.TrackerView).where(
            models.TrackerView.view_type.like(f"{TRACKER_VIEW_TYPE}%")
        )
        views: dict[str, models.TrackerView] = {}
        for view in self.session.execute(stmt).scalars():
            page = self._page_from_view_type(view.view_type)
            if page is None:
                continue
            key = self._view_type_for_page(page)
            if view.view_type != key:
                view.view_type = key
            views[key] = view
        return views

    def _build_blocks(
        self,
        rows: Iterable[TrackerRow],
        stage_counts: Counter[str],
        total_active: int,
        page_index: int,
        total_pages: int,
    ) -> list[dict]:
        now_str = datetime.now(tz=timezone.utc).strftime("%b %d · %H:%M UTC")
        blocks: list[dict] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": self._header_text(total_active, page_index, total_pages, now_str),
                },
            }
        ]

        if stage_counts:
            summary_chunks: list[str] = []
            summary_stage_values = {stage.value for stage in STAGE_SUMMARY_ORDER}

            def _stage_label_from_value(stage_value: str) -> str:
                try:
                    return stage_display(ApplicationStage(stage_value))
                except ValueError:
                    return stage_value.replace("_", " ").title()

            for stage in STAGE_SUMMARY_ORDER:
                value = stage_counts.get(stage.value, 0)
                summary_chunks.append(f"{stage_display(stage)} {value}")
            remaining = [
                f"{_stage_label_from_value(stage_value)} {count}"
                for stage_value, count in stage_counts.items()
                if stage_value not in summary_stage_values
            ]
            summary_chunks.extend(remaining)
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": " · ".join(summary_chunks),
                        }
                    ],
                }
            )

        blocks.append({"type": "divider"})

        row_list = list(rows)
        if not row_list:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "No active tracked jobs. Save a role to get started.",
                    },
                }
            )
            return blocks

        for row in row_list:
            blocks.append(self._build_row_block(row))

        if total_active > len(row_list):
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"Showing latest {len(row_list)} of {total_active} applications.",
                        }
                    ],
                }
            )

        return blocks

    def _build_row_block(self, row: TrackerRow) -> dict:
        score_display = f"`{row.score:.2f}`" if row.score is not None else "`—`"
        updated_str = row.updated_at.astimezone(timezone.utc).strftime("%b %d · %H:%M UTC")
        text = (
            f"*{row.job_title}* · {row.company}\n"
            f"Stage: `{stage_display(row.stage)}` · Score: {score_display}\n"
            f"Updated {updated_str} · <{row.url}|Job posting> · `{row.human_id}` · {row.location}"
        )
        value = json.dumps({"application_id": str(row.application_id)})
        return {
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": "Manage"},
                "action_id": "application_manage",
                "value": value,
            },
        }

    def _chunk_rows(self, rows: list[TrackerRow]) -> list[list[TrackerRow]]:
        chunks: list[list[TrackerRow]] = []
        for i in range(0, len(rows), ROWS_PER_PAGE):
            chunks.append(rows[i : i + ROWS_PER_PAGE])
        return chunks

    def _page_from_view_type(self, view_type: str) -> int | None:
        if view_type == TRACKER_VIEW_TYPE:
            return 1
        prefix = f"{TRACKER_VIEW_TYPE}:"
        if view_type.startswith(prefix):
            try:
                return int(view_type[len(prefix) :])
            except ValueError:
                return None
        return None

    def _view_type_for_page(self, page_index: int) -> str:
        if page_index == 1:
            return f"{TRACKER_VIEW_TYPE}:1"
        return f"{TRACKER_VIEW_TYPE}:{page_index}"

    def _header_text(self, total_active: int, page_index: int, total_pages: int, timestamp: str) -> str:
        if total_pages <= 1:
            return f"*Master Job Tracker* — {total_active} active\n_Last updated {timestamp}_"
        return (
            f"*Master Job Tracker* — {total_active} active (Page {page_index}/{total_pages})\n"
            f"_Last updated {timestamp}_"
        )
