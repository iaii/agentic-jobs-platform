"""rename cover_letter_vN artifact type to cover_letter_version

Revision ID: d1e2f3a4b5c6
Revises: c9f1a2b3d4e5
Create Date: 2026-05-22

"""
from alembic import op

revision = "d1e2f3a4b5c6"
down_revision = "c9f1a2b3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE artifacts SET type = 'cover_letter_version' WHERE type = 'cover_letter_vN'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE artifacts SET type = 'cover_letter_vN' WHERE type = 'cover_letter_version'"
    )
