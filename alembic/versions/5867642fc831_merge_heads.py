"""merge heads

Revision ID: 5867642fc831
Revises: 1b761e5d2c83, d426e18a4a9c
Create Date: 2025-12-29 02:22:39.771324

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5867642fc831'
down_revision: Union[str, None] = ('1b761e5d2c83', 'd426e18a4a9c')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
