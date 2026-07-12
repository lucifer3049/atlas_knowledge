"""MessageRepository:messages 表唯一 SQL 出口。

T1.2 僅需依 conversation 讀取(keyset 分頁);訊息寫入於 T1.4(SSE chat)。
"""
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import keyset_before
from app.infrastructure.db.models import Message


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_page(
        self,
        conversation_id: UUID,
        *,
        limit: int,
        cursor: tuple[datetime, UUID] | None,
    ) -> list[Message]:
        stmt = select(Message).where(Message.conversation_id == conversation_id)
        if cursor is not None:
            ts, id_ = cursor
            stmt = stmt.where(keyset_before(Message.created_at, Message.id, ts, id_))
        stmt = stmt.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit + 1)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
