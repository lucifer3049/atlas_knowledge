"""SQLAlchemy 2.0 models(權威 schema = PHASE_1 §4.1 DDL;不一致以 DDL 為準)。

T1.1 僅落 users / refresh_tokens 兩表;conversations / messages / model_usage_logs
於後續 ticket(T1.2 / T1.4)加入。Base 為全 Phase 共用宣告,NEVER 另建平行 Base。
"""
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.ids import new_id


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
