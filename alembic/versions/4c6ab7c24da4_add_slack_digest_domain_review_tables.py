"""add slack digest + domain review tables

Revision ID: 4c6ab7c24da4
Revises: 4dd2f4e2a91b
Create Date: 2025-10-22 16:56:29.171310

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4c6ab7c24da4'
down_revision: Union[str, None] = '4dd2f4e2a91b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
