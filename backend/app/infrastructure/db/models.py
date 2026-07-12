"""SQLAlchemy 2.0 models(權威 schema = PHASE_1 §4.1 DDL;不一致以 DDL 為準)。

T1.1 僅落 users / refresh_tokens 兩表;conversations / messages / model_usage_logs
於後續 ticket(T1.2 / T1.4)加入。Base 為全 Phase 共用宣告,NEVER 另建平行 Base。
"""
from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Uuid, func, text
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
