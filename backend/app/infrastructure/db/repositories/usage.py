"""UsageRepository:model_usage_logs 表唯一 SQL 出口(§4.1、T1.4)。

對 conversation/message 為軟引用(無 FK,§D3);對話刪除後用量統計仍存在。
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import ModelUsageLog


class UsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, log: ModelUsageLog) -> None:
        self._session.add(log)
        await self._session.flush()
