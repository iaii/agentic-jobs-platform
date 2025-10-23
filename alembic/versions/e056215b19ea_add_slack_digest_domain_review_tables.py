"""add slack digest + domain review tables

Revision ID: e056215b19ea
Revises: 5813a7c109d6
Create Date: 2025-10-22 17:29:19.864655

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e056215b19ea'
down_revision: Union[str, None] = '5813a7c109d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
