"""add autofill tasks table

Revision ID: 3c5c7f4f7ab8
Revises: 5867642fc831
Create Date: 2025-12-29 03:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "3c5c7f4f7ab8"
down_revision: Union[str, None] = "5867642fc831"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    autofill_status = sa.Enum(
        "queued",
        "in_progress",
        "ready",
        "blocked",
        "failed",
        "skipped",
        name="autofill_task_status",
        native_enum=False,
    )
    autofill_mode = sa.Enum(
        "autofill",
        "open_tabs",
        name="autofill_mode",
        native_enum=False,
    )
    bind = op.get_bind()
    autofill_status.create(bind, checkfirst=True)
    autofill_mode.create(bind, checkfirst=True)

    op.create_table(
        "autofill_tasks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("application_id", sa.UUID(), nullable=False),
        sa.Column("status", autofill_status, nullable=False),
        sa.Column("mode", autofill_mode, nullable=False),
        sa.Column("domain_root", sa.String(length=255), nullable=False),
        sa.Column("payload_path", sa.String(length=1024), nullable=False),
        sa.Column("resume_path", sa.String(length=1024), nullable=True),
        sa.Column("cover_letter_path", sa.String(length=1024), nullable=True),
        sa.Column("final_url", sa.String(length=1024), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_autofill_tasks_application_id",
        "autofill_tasks",
        ["application_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_autofill_tasks_application_id", table_name="autofill_tasks")
    op.drop_table("autofill_tasks")
    op.execute("DROP TYPE IF EXISTS autofill_task_status")
    op.execute("DROP TYPE IF EXISTS autofill_mode")
