"""跨測試共用的 fake adapters(PHASE_2 §12.1)。

`FakeEmbeddingProvider` 為檢索/嵌入測試的決定性基準:相同文字必得相同單位向量,
故「查詢與目標 chunk 同文字時必為 top1」可被斷言(T2.6 檢索測試依賴此性質)。
維度預設對齊 `EMBEDDING_DIM`(= DB `vector(n)` 欄位),否則落庫會被 pgvector 拒絕。
"""
import hashlib
import math
from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID

from app.application.retrieval_service import RetrievalService
from app.domain.entities.chunk import RetrievedChunk
from app.domain.ports.chunk_index import ChunkIndex
from app.domain.ports.embedding import EmbeddingError
from app.domain.ports.llm import ProviderErrorCode
from app.domain.ports.reranker import NoopReranker
from app.infrastructure.db.models import EMBEDDING_DIM
from app.infrastructure.embedding.cache import QueryEmbeddingCache


class FakeEmbeddingProvider:
    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim
        self.version = f"fake@{dim}"
        self.calls = 0
        self.embedded_texts: list[str] = []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        self.embedded_texts.extend(texts)
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        # sha256 只有 32 bytes;以計數器串接反覆 hash 展開至 dim,維持決定性。
        seed = text.encode("utf-8")
        buffer = bytearray()
        counter = 0
        while len(buffer) < self.dim:
            buffer.extend(hashlib.sha256(seed + counter.to_bytes(4, "big")).digest())
            counter += 1
        raw = [byte + 1 for byte in buffer[: self.dim]]  # +1 避免全零向量
        norm = math.sqrt(sum(x * x for x in raw))
        return [x / norm for x in raw]


class FailingEmbeddingProvider:
    """embed 一律失敗;驗證檢索失敗轉 SSE error(§9.1 末)。"""

    def __init__(self, code: ProviderErrorCode = "transient") -> None:
        self.dim = EMBEDDING_DIM
        self.version = "fake@fail"
        self._code = code

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise EmbeddingError(self._code, "embedding 服務暫時無法使用")


class FakeRedis:
    """in-memory redis(單元測試不碰外部服務;只需 get/set)。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value


class FakeChunkIndex:
    """ChunkIndex fake:回傳預設好的 chunks,並記錄收到的檢索參數。"""

    def __init__(self, chunks: Sequence[RetrievedChunk] = ()) -> None:
        self.chunks = list(chunks)
        self.calls: list[dict[str, Any]] = []

    async def hybrid_search(
        self,
        *,
        user_id: UUID,
        source_ids: Sequence[UUID] | None,
        query_tokens: str,
        query_embedding: list[float],
        top_n: int,
    ) -> list[RetrievedChunk]:
        self.calls.append(
            {
                "user_id": user_id,
                "source_ids": None if source_ids is None else list(source_ids),
                "query_tokens": query_tokens,
                "query_embedding": query_embedding,
                "top_n": top_n,
            }
        )
        return self.chunks[:top_n]


def fake_retrieval(
    chunk_index: ChunkIndex | None = None,
    *,
    embedding: FakeEmbeddingProvider | FailingEmbeddingProvider | None = None,
) -> RetrievalService:
    """組真實 RetrievalService + fake adapters(檢索流程本身一併受測)。

    `chunk_index` 亦可傳真實的 `PgVectorChunkIndex`(評測腳本測試即如此:只把
    embedding 換成 fake,檢索 SQL 走真件)。
    """
    cache = QueryEmbeddingCache(
        cast(Any, FakeRedis()), version="fake@test", ttl_s=60
    )
    return RetrievalService(
        chunk_index=chunk_index or FakeChunkIndex(),
        embedding=embedding or FakeEmbeddingProvider(),
        cache=cache,
        reranker=NoopReranker(),
    )
