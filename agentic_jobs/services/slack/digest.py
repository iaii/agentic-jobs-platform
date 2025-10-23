from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from uuid import UUID


def _format_score_chip(score: float) -> str:
    return f"*Score:* `{score:.2f}`"


@dataclass(slots=True)
class DigestRow:
    job_id: UUID
    title: str
    company: str
    location: str
    url: str
    score: float
    rationale: str


def build_digest_blocks(rows: Iterable[DigestRow]) -> list[dict]:
    blocks: list[dict] = []
    for idx, row in enumerate(rows, start=1):
        rationale = row.rationale
        if len(rationale) > 140:
            rationale = rationale[:137] + "..."
        blocks.append(
            {
                "type": "section",
                "block_id": f"digest_row_{row.job_id}",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{idx}. {row.title}* · {row.company} · {row.location}\n"
                        f"{_format_score_chip(row.score)}\n"
                        f"_{rationale}_"
                    ),
                },
            }
        )
        blocks.append(
            {
                "type": "actions",
                "block_id": f"digest_actions_{row.job_id}",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "open_jd",
                        "text": {"type": "plain_text", "text": "Open JD"},
                        "url": row.url,
                    },
                    {
                        "type": "button",
                        "action_id": "save_to_tracker",
                        "text": {"type": "plain_text", "text": "Save to Tracker"},
                        "style": "primary",
                        "value": str(row.job_id),
                    },
                ],
            }
        )
    if not blocks:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "No new roles matched this cycle.",
                },
            }
        )
    return blocks


@dataclass(slots=True)
class NeedsReviewCard:
    domain_root: str
    sample_url: str
    company_name: str | None
    score: int
    verdict: str


def build_needs_review_blocks(card: NeedsReviewCard) -> list[dict]:
    text_lines = [
        f"*Domain:* `{card.domain_root}`",
        f"*Verdict:* {card.verdict} ({card.score})",
        f"*Sample:* <{card.sample_url}|Job posting>",
    ]
    if card.company_name:
        text_lines.insert(1, f"*Company:* {card.company_name}")

    blocks = [
        {
            "type": "section",
            "block_id": f"needs_review_{card.domain_root}",
            "text": {"type": "mrkdwn", "text": "\n".join(text_lines)},
        },
        {
            "type": "actions",
            "block_id": f"needs_review_actions_{card.domain_root}",
            "elements": [
                {
                    "type": "button",
                    "action_id": "needs_review_approve",
                    "style": "primary",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "value": card.domain_root,
                },
                {
                    "type": "button",
                    "action_id": "needs_review_reject",
                    "style": "danger",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "value": card.domain_root,
                },
            ],
        },
    ]
    return blocks
