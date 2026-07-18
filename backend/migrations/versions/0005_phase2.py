"""T2.1 Phase 2 資料表(權威 = PHASE_2 §4.1 DDL)

knowledge_sources / documents / document_chunks / ingestion_jobs / message_citations。
HNSW、GIN、partial unique 等 autogenerate 產不出的索引一律 op.execute() 原文 SQL。
downgrade NEVER drop extension vector(可能被他表共用),只 drop 本階段表。

Revision ID: 0005_phase2
Revises: 0004_model_usage_logs
Create Date: 2026-07-18

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from app.infrastructure.db.models import EMBEDDING_DIM

# revision identifiers, used by Alembic.
revision: str = "0005_phase2"
down_revision: str | None = "0004_model_usage_logs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("create extension if not exists vector")

    op.create_table(
        "knowledge_sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("type in ('upload')", name="ck_sources_type"),
    )
    op.create_index("ix_sources_owner", "knowledge_sources", ["owner_id"])
    # 懶建預設「我的上傳」來源的併發防線(§11 補遺):每 owner 至多一列 type='upload'。
    op.execute(
        "create unique index ux_sources_owner_default on knowledge_sources (owner_id) "
        "where type = 'upload'"
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "source_id",
            sa.Uuid(),
            sa.ForeignKey("knowledge_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "uploaded_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("checksum", sa.CHAR(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("meta", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status in ('pending','parsing','chunking','embedding','ready','failed')",
            name="ck_documents_status",
        ),
    )
    op.create_index(
        "ux_documents_owner_checksum", "documents", ["uploaded_by", "checksum"], unique=True
    )
    op.create_index("ix_documents_source", "documents", ["source_id"])
    op.create_index("ix_documents_status", "documents", ["status"])

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("meta", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),  # D11
        sa.Column("embedding_version", sa.String(32), nullable=True),
        sa.Column("tsv", postgresql.TSVECTOR(), nullable=False),
        sa.UniqueConstraint("document_id", "seq", name="ux_chunks_doc_seq"),
    )
    op.create_index("ix_chunks_document", "document_chunks", ["document_id"])
    op.execute(
        "create index ix_chunks_embedding on document_chunks "
        "using hnsw (embedding vector_cosine_ops) with (m = 16, ef_construction = 64)"
    )
    op.execute("create index ix_chunks_tsv on document_chunks using gin (tsv)")

    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(8), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("stage in ('parse','chunk','embed')", name="ck_jobs_stage"),
        sa.CheckConstraint(
            "status in ('queued','running','succeeded','failed')", name="ck_jobs_status"
        ),
        sa.UniqueConstraint("document_id", "stage", name="ux_jobs_doc_stage"),
    )

    op.create_table(
        "message_citations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "message_id",
            sa.Uuid(),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # chunk_id / document_id 為軟引用(無 FK,D7)。
        sa.Column("chunk_id", sa.Uuid(), nullable=True),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("score", sa.Double(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_citations_message", "message_citations", ["message_id"])


def downgrade() -> None:
    op.drop_table("message_citations")
    op.drop_table("ingestion_jobs")
    op.drop_table("document_chunks")
    op.drop_table("documents")
    op.drop_table("knowledge_sources")
    # extension vector 保留:可能被他表 / 他遷移共用。
