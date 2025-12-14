"""add application feedback

Revision ID: f72b093d4d4a
Revises: e056215b19ea
Create Date: 2025-10-23 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f72b093d4d4a"
down_revision: Union[str, None] = "e056215b19ea"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    feedback_role = sa.Enum("user", "assistant", "system", name="feedback_role", native_enum=False)
    feedback_role.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "application_feedback",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("application_id", sa.UUID(), nullable=False),
        sa.Column("role", feedback_role, nullable=False),
        sa.Column("author", sa.String(length=255), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_application_feedback_application_id",
        "application_feedback",
        ["application_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_application_feedback_application_id", table_name="application_feedback")
    op.drop_table("application_feedback")
    op.execute("DROP TYPE IF EXISTS feedback_role")
