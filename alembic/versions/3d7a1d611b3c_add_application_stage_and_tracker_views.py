"""add application stage and tracker views

Revision ID: 3d7a1d611b3c
Revises: f72b093d4d4a
Create Date: 2024-12-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3d7a1d611b3c"
down_revision: Union[str, None] = "f72b093d4d4a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


application_stage = sa.Enum(
    "interested",
    "cover_letter_in_progress",
    "cover_letter_finalized",
    "submitted",
    "interviewing",
    "accepted",
    "rejected",
    name="application_stage",
    native_enum=False,
)


def upgrade() -> None:
    application_stage.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "applications",
        sa.Column("stage", application_stage, nullable=True),
    )
    op.execute("""
        UPDATE applications
        SET stage = 'interested'
        WHERE stage IS NULL
    """)
    op.alter_column("applications", "stage", nullable=False)

    op.create_table(
        "tracker_views",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("view_type", sa.String(length=64), nullable=False),
        sa.Column("slack_channel_id", sa.String(length=64), nullable=False),
        sa.Column("slack_message_ts", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("view_type", name="uq_tracker_views_view_type"),
    )


def downgrade() -> None:
    op.drop_table("tracker_views")
    op.drop_column("applications", "stage")
    application_stage.drop(op.get_bind(), checkfirst=True)
