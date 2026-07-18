"""KnowledgeSourceRepository:knowledge_sources 表唯一 SQL 出口;ownership 過濾在此(§C.2)。"""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import KnowledgeSource

DEFAULT_SOURCE_NAME = "我的上傳"
DEFAULT_SOURCE_TYPE = "upload"


class KnowledgeSourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_owned(self, owner_id: UUID, source_id: UUID) -> KnowledgeSource | None:
        result = await self._session.execute(
            select(KnowledgeSource).where(
                KnowledgeSource.id == source_id,
                KnowledgeSource.owner_id == owner_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_default(self, owner_id: UUID) -> KnowledgeSource | None:
        result = await self._session.execute(
            select(KnowledgeSource).where(
                KnowledgeSource.owner_id == owner_id,
                KnowledgeSource.type == DEFAULT_SOURCE_TYPE,
            )
        )
        return result.scalar_one_or_none()

    async def create_default(self, owner_id: UUID) -> KnowledgeSource:
        # 併發由 partial unique index ux_sources_owner_default 擋下(§11 補遺);
        # 呼叫端負責 catch IntegrityError 後重查。
        source = KnowledgeSource(
            owner_id=owner_id, name=DEFAULT_SOURCE_NAME, type=DEFAULT_SOURCE_TYPE
        )
        self._session.add(source)
        await self._session.flush()
        return source
