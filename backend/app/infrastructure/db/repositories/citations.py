"""MessageCitationRepository:message_citations 表唯一 SQL 出口(T2.6;PHASE_2 §4.1 D7)。

快照策略(D7):存 filename / snippet / score,chunk_id 與 document_id 為**軟引用**
(無 FK)——文件刪除後歷史對話的引用仍可讀。
訊息列表一次撈整頁的 citations(`message_id = any(:ids)`),NEVER 逐則查詢(§16 無 N+1)。
(§3 目錄未列本檔;SQL MUST 只在 repository,故與其他表一致各自成檔。)
"""
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import MessageCitation

SNIPPET_MAX_CHARS = 200  # §4.1:snippet = 前 200 字快照


class MessageCitationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add_all(self, citations: Sequence[MessageCitation]) -> None:
        """bulk insert(交易邊界由呼叫端掌握;與 assistant message 同一 TXN B)。"""
        if citations:
            self._session.add_all(list(citations))

    async def list_for_messages(
        self, message_ids: Sequence[UUID]
    ) -> dict[UUID, list[MessageCitation]]:
        """整頁一次載入;回傳 message_id → 依 rank 排序的 citations。"""
        if not message_ids:
            return {}
        result = await self._session.execute(
            select(MessageCitation)
            .where(MessageCitation.message_id.in_(list(message_ids)))
            .order_by(MessageCitation.message_id, MessageCitation.rank)
        )
        grouped: dict[UUID, list[MessageCitation]] = {}
        for citation in result.scalars().all():
            grouped.setdefault(citation.message_id, []).append(citation)
        return grouped
