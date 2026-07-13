"""ConversationRepository:conversations 表唯一 SQL 出口;ownership 過濾在此(§5.3-5)。"""
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import keyset_before
from app.infrastructure.db.models import Conversation


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, user_id: UUID, title: str | None, model_alias: str) -> Conversation:
        conv = Conversation(user_id=user_id, title=title, model_alias=model_alias)
        self._session.add(conv)
        await self._session.flush()
        return conv

    async def get_owned(self, user_id: UUID, conversation_id: UUID) -> Conversation | None:
        result = await self._session.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_page(
        self,
        user_id: UUID,
        *,
        limit: int,
        cursor: tuple[datetime, UUID] | None,
    ) -> list[Conversation]:
        stmt = select(Conversation).where(Conversation.user_id == user_id)
        if cursor is not None:
            ts, id_ = cursor
            stmt = stmt.where(keyset_before(Conversation.updated_at, Conversation.id, ts, id_))
        stmt = stmt.order_by(Conversation.updated_at.desc(), Conversation.id.desc()).limit(
            limit + 1
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def delete(self, conversation: Conversation) -> None:
        # DB 端 ON DELETE CASCADE 連帶刪除 messages。
        await self._session.delete(conversation)

    async def bump_updated_at(self, conversation_id: UUID) -> None:
        """明確更新 updated_at(§8-5;插入 assistant 訊息不會動 conversation 列,
        故側欄排序需主動 bump)。"""
        await self._session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(updated_at=func.now())
        )
