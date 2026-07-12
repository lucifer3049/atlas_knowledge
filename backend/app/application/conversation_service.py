"""ConversationService:conversations / messages 的 use case(PHASE_1 §10、T1.2)。

分層:router 只做序列化,SQL 只在 repository,ownership 過濾在 repository。
model_alias 以 config/models.yaml 驗證(§R R2),不合法回 422 validation_error(不新增碼)。
"""
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConversationNotFound, ValidationError
from app.core.model_registry import alias_exists, default_alias
from app.core.pagination import decode_cursor, paginate
from app.domain.entities.auth_context import AuthContext
from app.infrastructure.db.models import Conversation, Message
from app.infrastructure.db.repositories.conversations import ConversationRepository
from app.infrastructure.db.repositories.messages import MessageRepository


def _conv_key(conv: Conversation) -> tuple[datetime, UUID]:
    return conv.updated_at, conv.id


def _msg_key(msg: Message) -> tuple[datetime, UUID]:
    return msg.created_at, msg.id


class ConversationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._conversations = ConversationRepository(session)
        self._messages = MessageRepository(session)

    async def create(
        self, ctx: AuthContext, *, title: str | None, model_alias: str | None
    ) -> Conversation:
        alias = model_alias or default_alias()
        if not alias_exists(alias):
            raise ValidationError("model_alias 無效")
        conv = await self._conversations.create(
            user_id=ctx.user_id, title=title, model_alias=alias
        )
        await self._session.commit()
        return conv

    async def get(self, ctx: AuthContext, conversation_id: UUID) -> Conversation:
        conv = await self._conversations.get_owned(ctx.user_id, conversation_id)
        if conv is None:
            raise ConversationNotFound()
        return conv

    async def list_conversations(
        self, ctx: AuthContext, *, limit: int, cursor: str | None
    ) -> tuple[list[Conversation], str | None]:
        keyset = decode_cursor(cursor) if cursor else None
        rows = await self._conversations.list_page(ctx.user_id, limit=limit, cursor=keyset)
        return paginate(rows, limit, _conv_key)

    async def rename(self, ctx: AuthContext, conversation_id: UUID, title: str) -> Conversation:
        conv = await self.get(ctx, conversation_id)
        conv.title = title
        await self._session.commit()
        # updated_at 由 server-side onupdate=now() 產生,commit 後為過期狀態;
        # 於 async 脈絡內 refresh 取回新值,避免序列化時觸發 lazy load(MissingGreenlet)。
        await self._session.refresh(conv)
        return conv

    async def delete(self, ctx: AuthContext, conversation_id: UUID) -> None:
        conv = await self.get(ctx, conversation_id)
        await self._conversations.delete(conv)
        await self._session.commit()

    async def list_messages(
        self, ctx: AuthContext, conversation_id: UUID, *, limit: int, cursor: str | None
    ) -> tuple[list[Message], str | None]:
        await self.get(ctx, conversation_id)  # ownership → 無權/查無一律 404
        keyset = decode_cursor(cursor) if cursor else None
        rows = await self._messages.list_page(conversation_id, limit=limit, cursor=keyset)
        return paginate(rows, limit, _msg_key)
