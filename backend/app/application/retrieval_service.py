"""RetrievalService:hybrid 檢索的 use case(T2.6;PHASE_2 §9.1)。

流程(§9.1):
1. tokens = jieba 斷詞(索引側同模式,R15)
2. qvec = query embedding 快取(TTL 1h,D9)或 provider
3. chunk_index.hybrid_search(RRF;ownership 過濾在 SQL 內)
4. reranker.rerank(P2 為 Noop)

`EmbeddingError` 一律上拋,由 orchestrator 轉為 SSE `error`(code 沿用 provider 五類,
§9.1 末)。本層 NEVER 出現 SQL(§16)。
"""
from collections.abc import Sequence
from uuid import UUID

import structlog

from app.domain.entities.auth_context import AuthContext
from app.domain.entities.chunk import RetrievedChunk
from app.domain.ports.chunk_index import ChunkIndex
from app.domain.ports.embedding import EmbeddingProvider
from app.domain.ports.reranker import Reranker
from app.infrastructure.embedding.cache import QueryEmbeddingCache, embed_query
from app.infrastructure.vector.tokenizer import index_tokens

_logger = structlog.get_logger()

# query 送 embed 前截前 2000 字(v1.2 §10 補遺):長貼文問句對向量無幫助,且省 token。
QUERY_EMBED_MAX_CHARS = 2000


class RetrievalService:
    def __init__(
        self,
        *,
        chunk_index: ChunkIndex,
        embedding: EmbeddingProvider,
        cache: QueryEmbeddingCache,
        reranker: Reranker,
    ) -> None:
        self._chunk_index = chunk_index
        self._embedding = embedding
        self._cache = cache
        self._reranker = reranker

    async def retrieve(
        self,
        ctx: AuthContext,
        query: str,
        *,
        source_ids: Sequence[UUID] | None,
        top_n: int,
    ) -> list[RetrievedChunk]:
        tokens = index_tokens(query)
        qvec = await embed_query(self._embedding, self._cache, query[:QUERY_EMBED_MAX_CHARS])
        chunks = await self._chunk_index.hybrid_search(
            user_id=ctx.user_id,
            source_ids=source_ids,
            query_tokens=tokens,
            query_embedding=qvec,
            top_n=top_n,
        )
        result = await self._reranker.rerank(query, chunks, top_n)
        # NEVER log query 原文與 chunk 內容(§16);只留量體。
        _logger.info(
            "retrieval.search",
            user_id=str(ctx.user_id),
            query_chars=len(query),
            hits=len(result),
        )
        return result
