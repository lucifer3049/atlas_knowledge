"""MessageRepository:messages 表唯一 SQL 出口。"""
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import keyset_before
from app.infrastructure.db.models import Message


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, message: Message) -> None:
        """插入單則訊息並 flush。client_message_id 唯一衝突會於此 raise IntegrityError,
        由 service 轉為 DuplicateMessage(409);呼叫端負責交易邊界。"""
        self._session.add(message)
        await self._session.flush()

    async def first_of_role(self, conversation_id: UUID, role: str) -> Message | None:
        """該對話中最早的一則指定角色訊息(標題生成取首輪 user/assistant;T1.7)。"""
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id, Message.role == role)
            .order_by(Message.created_at, Message.id)
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_recent(self, conversation_id: UUID, *, limit: int) -> list[Message]:
        """取最近 limit 則,回傳為時間正序(asc)供組 prompt 上下文(§8-1)。"""
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        rows.reverse()
        return rows

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
