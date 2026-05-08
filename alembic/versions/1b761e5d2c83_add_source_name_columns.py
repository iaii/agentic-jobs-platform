"""add source name columns to jobs and job_sources"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "1b761e5d2c83"
down_revision: Union[str, None] = "6a0d6ffb2d75"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("source_name", sa.String(length=128), nullable=False, server_default="unknown"),
    )
    op.add_column(
        "job_sources",
        sa.Column("source_name", sa.String(length=128), nullable=False, server_default="unknown"),
    )
    op.alter_column("jobs", "source_name", server_default=None)
    op.alter_column("job_sources", "source_name", server_default=None)


def downgrade() -> None:
    op.drop_column("job_sources", "source_name")
    op.drop_column("jobs", "source_name")
