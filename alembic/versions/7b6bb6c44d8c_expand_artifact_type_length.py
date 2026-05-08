"""expand artifact type column for final pdf enum

Revision ID: 7b6bb6c44d8c
Revises: 3c5c7f4f7ab8
Create Date: 2026-01-10 20:07:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7b6bb6c44d8c"
down_revision: Union[str, None] = "3c5c7f4f7ab8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "artifacts",
        "type",
        existing_type=sa.VARCHAR(length=20),
        type_=sa.String(length=64),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "artifacts",
        "type",
        existing_type=sa.String(length=64),
        type_=sa.VARCHAR(length=20),
        existing_nullable=False,
    )
