"""add resume_text_path to profile_files

Revision ID: c9f1a2b3d4e5
Revises: b3c4d5e6f7a8
Create Date: 2026-05-20

"""
from alembic import op
import sqlalchemy as sa

revision = "c9f1a2b3d4e5"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profile_files",
        sa.Column("resume_text_path", sa.String(1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("profile_files", "resume_text_path")
