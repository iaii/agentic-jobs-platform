"""add slack digest + domain review tables

Revision ID: aef12ff80b14
Revises: e056215b19ea
Create Date: 2025-10-22 17:30:32.041897

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'aef12ff80b14'
down_revision: Union[str, None] = 'e056215b19ea'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
