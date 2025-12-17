"""normalize application_stage enum casing

Revision ID: 6a0d6ffb2d75
Revises: 3d7a1d611b3c
Create Date: 2024-12-18 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "6a0d6ffb2d75"
down_revision: Union[str, None] = "3d7a1d611b3c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Normalize any stray uppercase values back to lowercase to match enum values.
    op.execute("UPDATE applications SET stage = lower(stage)")


def downgrade() -> None:
    op.execute("UPDATE applications SET stage = upper(stage)")
