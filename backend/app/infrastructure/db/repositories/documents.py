"""DocumentRepository:documents 表唯一 SQL 出口;ownership 過濾在此(§C.2、§5.3-5)。"""
from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import keyset_before
from app.infrastructure.db.models import Document


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        document_id: UUID,
        source_id: UUID,
        uploaded_by: UUID,
        filename: str,
        mime: str,
        size_bytes: int,
        storage_key: str,
        checksum: str,
    ) -> Document:
        # id 由呼叫端先行決定:storage key 需在落庫前組出(§5 key 佈局)。
        doc = Document(
            id=document_id,
            source_id=source_id,
            uploaded_by=uploaded_by,
            filename=filename,
            mime=mime,
            size_bytes=size_bytes,
            storage_key=storage_key,
            checksum=checksum,
            status="pending",
        )
        self._session.add(doc)
        await self._session.flush()
        return doc

    async def get_owned(self, user_id: UUID, document_id: UUID) -> Document | None:
        result = await self._session.execute(
            select(Document).where(
                Document.id == document_id,
                Document.uploaded_by == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_checksum(self, user_id: UUID, checksum: str) -> Document | None:
        # D8:去重鍵 =(uploaded_by, checksum),owner 範圍語意。
        result = await self._session.execute(
            select(Document).where(
                Document.uploaded_by == user_id,
                Document.checksum == checksum,
            )
        )
        return result.scalar_one_or_none()

    async def list_page(
        self,
        user_id: UUID,
        *,
        limit: int,
        cursor: tuple[datetime, UUID] | None,
        status: str | None = None,
    ) -> list[Document]:
        stmt = select(Document).where(Document.uploaded_by == user_id)
        if status is not None:
            stmt = stmt.where(Document.status == status)
        if cursor is not None:
            ts, id_ = cursor
            stmt = stmt.where(keyset_before(Document.created_at, Document.id, ts, id_))
        stmt = stmt.order_by(Document.created_at.desc(), Document.id.desc()).limit(limit + 1)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def delete(self, document: Document) -> None:
        # DB 端 ON DELETE CASCADE 連帶刪除 document_chunks / ingestion_jobs;
        # storage 清理走背景 purge_document(D12)。
        await self._session.delete(document)

    # --- pipeline(T2.5;無 owner 過濾:僅供背景任務使用)--------------------

    async def get(self, document_id: UUID) -> Document | None:
        return await self._session.get(Document, document_id)

    async def claim(
        self, document_id: UUID, *, allowed_from: Sequence[str], to: str
    ) -> bool:
        """條件更新推進狀態機(§8.1)。回傳 False = 0 rows(已被處理),任務直接 return。"""
        result = await self._session.execute(
            update(Document)
            .where(Document.id == document_id, Document.status.in_(allowed_from))
            .values(status=to, error=None, updated_at=func.now())
        )
        return cast("CursorResult[Any]", result).rowcount > 0

    async def set_status(self, document_id: UUID, status: str) -> None:
        await self._session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(status=status, updated_at=func.now())
        )

    async def set_failed(self, document_id: UUID, error: str) -> None:
        await self._session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(status="failed", error=error, updated_at=func.now())
        )
