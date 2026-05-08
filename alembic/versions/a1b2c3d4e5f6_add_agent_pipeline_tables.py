"""add agent pipeline tables

Revision ID: a1b2c3d4e5f6
Revises: 7b6bb6c44d8c
Create Date: 2026-04-16 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "7b6bb6c44d8c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pipeline_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "application_id",
            UUID(as_uuid=True),
            sa.ForeignKey("applications.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="running"),
        sa.Column("agent_log", JSONB, nullable=False, server_default="[]"),
        sa.Column("final_score", sa.Float, nullable=True),
        sa.Column("revision_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "agent_memories",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "application_id",
            UUID(as_uuid=True),
            sa.ForeignKey("applications.id"),
            nullable=True,
            index=True,
        ),
        sa.Column("memory_type", sa.String(length=32), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "vault_embeddings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("heading", sa.String(length=512), nullable=False),
        sa.Column("section_text", sa.Text, nullable=False),
        sa.Column("wikilinks", JSONB, nullable=False, server_default="[]"),
        sa.Column("embedding", JSONB, nullable=True),
        sa.Column("file_hash", sa.String(length=128), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("file_path", "heading", name="uq_vault_embedding_file_heading"),
    )

    op.create_table(
        "company_cache",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("domain", sa.String(length=255), nullable=False, unique=True),
        sa.Column("company_name", sa.String(length=255), nullable=False),
        sa.Column("scraped_data", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "scraped_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("ttl_hours", sa.Integer, nullable=False, server_default="168"),
    )


def downgrade() -> None:
    op.drop_table("company_cache")
    op.drop_table("vault_embeddings")
    op.drop_table("agent_memories")
    op.drop_table("pipeline_runs")
