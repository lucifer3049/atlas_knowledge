"""`ChunkIndex` port(凍結契約;PHASE_2 §5、§9.2)。

檢索的唯一抽象:vector(pgvector HNSW)+ FTS(jieba tsvector)兩腿以 RRF 融合,
實作於 `infrastructure/vector/pgvector_index.py`。**ownership 過濾一律在 SQL 內**
(§9.2;`meta.kind='law'` 全站共享為唯一例外,R16),NEVER 撈回 Python 再過濾。
本檔為純 domain,NEVER import 任何框架 / SDK(§C.2)。
"""
from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.domain.entities.chunk import RetrievedChunk


class ChunkIndex(Protocol):
    async def hybrid_search(
        self,
        *,
        user_id: UUID,
        source_ids: Sequence[UUID] | None,
        query_tokens: str,
        query_embedding: list[float],
        top_n: int,
    ) -> list[RetrievedChunk]:
        """`source_ids` None(或空)= 該 user 全部 enabled 來源。

        `query_tokens` 為以空白分隔的 jieba token 串(索引側與查詢側同模式,R15);
        轉成 tsquery 的組法屬 adapter 內部細節(§9.2)。
        """
        ...
