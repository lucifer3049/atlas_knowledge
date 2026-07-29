"""IngestionJobRepository:ingestion_jobs 表唯一 SQL 出口(T2.5;PHASE_2 §8.2)。

`ingestion_jobs` 為**純觀測紀錄,不參與控制流**(v1.2 §8 補遺):`documents.status` 是
狀態機的唯一權威。本表只回答「上次失敗在哪個 stage」以決定 retry 的入口(§8 stage-level
retry),以及提供每 stage 的 attempts / error 供人工診斷。
"""
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.infrastructure.db.models import IngestionJob

# stage 於 pipeline 中的先後;retry 取「最早的失敗 stage」以免跳過前置產物。
STAGE_ORDER: tuple[str, ...] = ("parse", "chunk", "embed")


class IngestionJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def mark_running(self, document_id: UUID, stage: str) -> None:
        """開始:upsert 該 (document_id, stage) 列並遞增 attempts(§8.2 末)。"""
        stmt = insert(IngestionJob).values(
            id=new_id(),
            document_id=document_id,
            stage=stage,
            status="running",
            attempts=1,
            error=None,
            started_at=func.now(),
            finished_at=None,
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                constraint="ux_jobs_doc_stage",
                set_={
                    "status": "running",
                    "attempts": IngestionJob.attempts + 1,
                    "error": None,
                    "started_at": func.now(),
                    "finished_at": None,
                },
            )
        )

    async def mark_finished(
        self, document_id: UUID, stage: str, *, status: str, error: str | None = None
    ) -> None:
        stmt = insert(IngestionJob).values(
            id=new_id(),
            document_id=document_id,
            stage=stage,
            status=status,
            attempts=1,
            error=error,
            finished_at=func.now(),
        )
        await self._session.execute(
            stmt.on_conflict_do_update(
                constraint="ux_jobs_doc_stage",
                set_={"status": status, "error": error, "finished_at": func.now()},
            )
        )

    async def first_failed_stage(self, document_id: UUID) -> str | None:
        """回傳最早的失敗 stage;無失敗紀錄回 None(retry 從 parse 重跑)。"""
        result = await self._session.execute(
            select(IngestionJob.stage).where(
                IngestionJob.document_id == document_id,
                IngestionJob.status == "failed",
            )
        )
        failed = set(result.scalars().all())
        return next((stage for stage in STAGE_ORDER if stage in failed), None)
