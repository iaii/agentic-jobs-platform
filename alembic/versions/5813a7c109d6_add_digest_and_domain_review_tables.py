"""add digest and domain review tables

Revision ID: 5813a7c109d6
Revises: 4c6ab7c24da4
Create Date: 2025-10-22 16:58:18.582598

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5813a7c109d6'
down_revision: Union[str, None] = '4c6ab7c24da4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    status_enum = sa.Enum(
        'PENDING',
        'APPROVED',
        'MUTED',
        name='domain_review_status',
        native_enum=False,
    )
    status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'digest_logs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('job_id', sa.UUID(), nullable=False),
        sa.Column('digest_date', sa.Date(), nullable=False),
        sa.Column('slack_channel_id', sa.String(length=64), nullable=False),
        sa.Column('slack_message_ts', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('job_id', 'digest_date', name='uq_digest_job_date'),
    )

    op.create_table(
        'domain_reviews',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('domain_root', sa.String(length=255), nullable=False),
        sa.Column('status', status_enum, nullable=False),
        sa.Column('slack_channel_id', sa.String(length=64), nullable=True),
        sa.Column('slack_message_ts', sa.String(length=32), nullable=True),
        sa.Column('company_name', sa.String(length=255), nullable=True),
        sa.Column('ats_type', sa.String(length=64), nullable=True),
        sa.Column('muted_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('domain_root'),
    )


def downgrade() -> None:
    op.drop_table('domain_reviews')
    op.drop_table('digest_logs')

    status_enum = sa.Enum(
        'PENDING',
        'APPROVED',
        'MUTED',
        name='domain_review_status',
        native_enum=False,
    )
    status_enum.drop(op.get_bind(), checkfirst=True)
