"""ChunkRepository:document_chunks 表唯一 SQL 出口(T2.4;PHASE_2 §8.2)。

寫入時 tsv 一律 = `to_tsvector('simple', jieba斷詞)`(索引側與查詢側同模式,§9.2 R15)。
embedding 先留 NULL(D11),由 embed 階段以 `embedding is null` 為游標分批回寫——
中斷重跑自然續傳(§8.2)。SQL 只在此,service / task 內 NEVER 裸 query。
"""
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.domain.entities.chunk import ChunkDraft
from app.infrastructure.db.models import DocumentChunk
from app.infrastructure.vector.tokenizer import index_tokens


class ChunkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_for_document(
        self, document_id: UUID, drafts: Sequence[ChunkDraft]
    ) -> None:
        """先刪後插(§8.2 冪等手段):重跑 chunk 階段 NEVER 產生重複 chunks。"""
        await self._session.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )
        if not drafts:
            return
        rows = [
            {
                "id": new_id(),
                "document_id": document_id,
                "seq": draft.seq,
                "text": draft.text,
                "tokens": draft.tokens,
                "meta": draft.meta,
                # tsv 為 server 端計算的 SQL 表達式;多列 VALUES 內聯,NEVER 走 executemany。
                "tsv": func.to_tsvector("simple", index_tokens(draft.text)),
            }
            for draft in drafts
        ]
        await self._session.execute(insert(DocumentChunk).values(rows))

    async def list_unembedded(self, document_id: UUID, *, limit: int) -> list[DocumentChunk]:
        result = await self._session.execute(
            select(DocumentChunk)
            .where(
                DocumentChunk.document_id == document_id,
                DocumentChunk.embedding.is_(None),
            )
            .order_by(DocumentChunk.seq)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_unembedded(self, document_id: UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(DocumentChunk)
            .where(
                DocumentChunk.document_id == document_id,
                DocumentChunk.embedding.is_(None),
            )
        )
        return int(result.scalar_one())

    async def set_embeddings(
        self, vectors: dict[UUID, list[float]], *, version: str
    ) -> None:
        """回寫一批 embedding;以 chunk id 定位,冪等(重跑覆寫同值)。"""
        for chunk_id, vector in vectors.items():
            await self._session.execute(
                update(DocumentChunk)
                .where(DocumentChunk.id == chunk_id)
                .values(embedding=vector, embedding_version=version)
            )
