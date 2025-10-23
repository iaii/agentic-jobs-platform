"""add slack digest + domain review tables

Revision ID: d426e18a4a9c
Revises: aef12ff80b14
Create Date: 2025-10-22 19:24:14.375828

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd426e18a4a9c'
down_revision: Union[str, None] = 'aef12ff80b14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
