"""SQLAlchemy 2.0 models(權威 schema = PHASE_1 §4.1 DDL;不一致以 DDL 為準)。

T1.1 僅落 users / refresh_tokens 兩表;conversations / messages / model_usage_logs
於後續 ticket(T1.2 / T1.4)加入。Base 為全 Phase 共用宣告,NEVER 另建平行 Base。
"""
from datetime import datetime
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Double,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.ids import new_id

# embedding 向量維度(PHASE_2 §4.2);migration 引用同一常數,變更 = 新遷移。
EMBEDDING_DIM = 1024

# DocumentChunk 有名為 text 的欄位,會在該 class 範圍遮蔽 sqlalchemy.text;
# Phase 2 的 JSONB 預設值一律用此模組常數。
_EMPTY_JSONB = text("'{}'::jsonb")


class Base(DeclarativeBase):
    type_annotation_map = {
        UUID: Uuid(as_uuid=True),
        datetime: TIMESTAMP(timezone=True),
        dict: JSONB,
    }


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255))  # 寫入前 MUST strip + lower
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), server_default=text("'user'"))
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    __table_args__ = (
        Index("ux_users_email", "email", unique=True),
        CheckConstraint("role in ('user','admin')", name="ck_users_role"),
    )


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str | None] = mapped_column(String(200))  # null = 尚未生成
    channel: Mapped[str] = mapped_column(String(16), server_default=text("'web'"))
    model_alias: Mapped[str] = mapped_column(String(64))
    # 註:ix_conversations_user_updated(user_id, updated_at DESC, id DESC)僅存在於
    #     migration(op.execute 原文 DDL);ORM class body 無法宣告 desc 索引(v1.2 §4.2 勘誤)。


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    content_meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    client_message_id: Mapped[UUID | None]
    provider: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(64))
    tokens_in: Mapped[int | None]
    tokens_out: Mapped[int | None]
    latency_ms: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    __table_args__ = (
        CheckConstraint(
            "role in ('system','user','assistant','tool')", name="ck_messages_role"
        ),
        Index("ix_messages_conv_created", "conversation_id", "created_at", "id"),
        Index(
            "ux_messages_client_id",
            "conversation_id",
            "client_message_id",
            unique=True,
            postgresql_where=text("client_message_id is not null"),
        ),
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[UUID] = mapped_column(index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256 hex,NEVER 明文
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    replaced_by: Mapped[UUID | None]
    user_agent: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ModelUsageLog(Base):
    # 對 conversation / message 為軟引用(無 FK,§D3);對話刪除後用量統計仍存在。
    __tablename__ = "model_usage_logs"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    conversation_id: Mapped[UUID | None]
    message_id: Mapped[UUID | None]
    channel: Mapped[str] = mapped_column(String(16), server_default=text("'web'"))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    tokens_in: Mapped[int | None]
    tokens_out: Mapped[int | None]
    latency_ms: Mapped[int | None]
    status: Mapped[str] = mapped_column(String(8))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    __table_args__ = (
        CheckConstraint("status in ('ok','error')", name="ck_usage_status"),
        Index("ix_usage_user_created", "user_id", "created_at"),
    )


# --- Phase 2:文件匯入 + RAG(權威 schema = PHASE_2 §4.1 DDL)---------------


class KnowledgeSource(TimestampMixin, Base):
    __tablename__ = "knowledge_sources"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(16))  # P3+ 擴充枚舉
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=_EMPTY_JSONB)
    enabled: Mapped[bool] = mapped_column(server_default=text("true"))
    __table_args__ = (
        CheckConstraint("type in ('upload')", name="ck_sources_type"),
        Index("ix_sources_owner", "owner_id"),
    )
    # 註:ux_sources_owner_default(owner_id) WHERE type='upload' 為 partial unique index,
    #     僅存在於 migration(§11 補遺:防併發重複建立預設「我的上傳」來源)。


class Document(TimestampMixin, Base):
    __tablename__ = "documents"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="CASCADE")
    )
    uploaded_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(100))  # canonical mime(由副檔名白名單推導)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    storage_key: Mapped[str] = mapped_column(String(500))  # 存 key,NEVER 存絕對路徑
    checksum: Mapped[str] = mapped_column(String(64))  # sha256 hex
    status: Mapped[str] = mapped_column(String(16), server_default=text("'pending'"))
    error: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=_EMPTY_JSONB)
    __table_args__ = (
        CheckConstraint(
            "status in ('pending','parsing','chunking','embedding','ready','failed')",
            name="ck_documents_status",
        ),
        Index("ux_documents_owner_checksum", "uploaded_by", "checksum", unique=True),  # D8
        Index("ix_documents_source", "source_id"),
        Index("ix_documents_status", "status"),
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    seq: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
    tokens: Mapped[int]
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=_EMPTY_JSONB)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))  # D11
    embedding_version: Mapped[str | None] = mapped_column(String(32))
    tsv: Mapped[str] = mapped_column(TSVECTOR)
    __table_args__ = (
        Index("ux_chunks_doc_seq", "document_id", "seq", unique=True),
        Index("ix_chunks_document", "document_id"),
    )
    # 註:ix_chunks_embedding(HNSW)與 ix_chunks_tsv(GIN)為 migration 原文 DDL。


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    stage: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(10))
    attempts: Mapped[int] = mapped_column(server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    __table_args__ = (
        CheckConstraint("stage in ('parse','chunk','embed')", name="ck_jobs_stage"),
        CheckConstraint(
            "status in ('queued','running','succeeded','failed')", name="ck_jobs_status"
        ),
        Index("ux_jobs_doc_stage", "document_id", "stage", unique=True),
    )


class MessageCitation(Base):
    # chunk_id / document_id 為軟引用(無 FK,D7):文件刪除後歷史對話的引用仍可讀。
    __tablename__ = "message_citations"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    message_id: Mapped[UUID] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    chunk_id: Mapped[UUID | None]
    document_id: Mapped[UUID | None]
    filename: Mapped[str] = mapped_column(String(255))
    snippet: Mapped[str] = mapped_column(Text)  # 前 200 字快照
    score: Mapped[float] = mapped_column(Double)  # RRF 分數
    rank: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    __table_args__ = (Index("ix_citations_message", "message_id"),)
