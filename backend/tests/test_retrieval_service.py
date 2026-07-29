"""RetrievalService 單元測試(fake adapters,不碰 DB;T2.6、PHASE_2 §9.1)。"""
from uuid import uuid4

import pytest

from app.application.retrieval_service import QUERY_EMBED_MAX_CHARS
from app.domain.entities.auth_context import AuthContext
from app.domain.entities.chunk import RetrievedChunk
from app.domain.ports.embedding import EmbeddingError
from tests.fakes import (
    FailingEmbeddingProvider,
    FakeChunkIndex,
    FakeEmbeddingProvider,
    fake_retrieval,
)

pytestmark = pytest.mark.anyio

_CTX = AuthContext(user_id=uuid4(), role="user", trace_id="trace-1")


def _chunk(rank: int) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename=f"{rank}.txt",
        text=f"內容 {rank}",
        score=1.0 / rank,
        rank=rank,
        meta={"kind": "document"},
    )


async def test_query_is_tokenized_with_jieba_for_fts_leg() -> None:
    index = FakeChunkIndex()
    await fake_retrieval(index).retrieve(_CTX, "台北市的首都", source_ids=None, top_n=8)

    tokens = index.calls[0]["query_tokens"].split()
    # 索引側與查詢側同模式(R15):cut_for_search 會產出「台北」等子詞
    assert "台北" in tokens and "首都" in tokens


async def test_long_query_is_truncated_before_embedding() -> None:
    embedding = FakeEmbeddingProvider()
    index = FakeChunkIndex()
    long_query = "字" * (QUERY_EMBED_MAX_CHARS + 500)
    await fake_retrieval(index, embedding=embedding).retrieve(
        _CTX, long_query, source_ids=None, top_n=8
    )

    # query 送 embed 前截前 2000 字(v1.2 §10 補遺)
    assert len(embedding.embedded_texts[0]) == QUERY_EMBED_MAX_CHARS


async def test_source_ids_and_top_n_are_passed_through() -> None:
    index = FakeChunkIndex([_chunk(1)])
    source_ids = [uuid4(), uuid4()]
    await fake_retrieval(index).retrieve(_CTX, "問句", source_ids=source_ids, top_n=3)

    call = index.calls[0]
    assert call["source_ids"] == source_ids
    assert call["top_n"] == 3
    assert call["user_id"] == _CTX.user_id


async def test_noop_reranker_keeps_order_and_caps_top_n() -> None:
    chunks = [_chunk(i) for i in range(1, 6)]
    index = FakeChunkIndex(chunks)
    result = await fake_retrieval(index).retrieve(_CTX, "問句", source_ids=None, top_n=3)

    # P2 只有 NoopReranker:原樣回傳前 top_n(§5)
    assert [c.rank for c in result] == [1, 2, 3]


async def test_embedding_error_propagates_to_caller() -> None:
    service = fake_retrieval(FakeChunkIndex(), embedding=FailingEmbeddingProvider("auth"))
    with pytest.raises(EmbeddingError) as exc_info:
        await service.retrieve(_CTX, "問句", source_ids=None, top_n=8)
    # code 沿用 provider 五類,由 orchestrator 轉 SSE error(§9.1 末)
    assert exc_info.value.code == "auth"


async def test_cache_hit_skips_provider_call() -> None:
    embedding = FakeEmbeddingProvider()
    service = fake_retrieval(FakeChunkIndex(), embedding=embedding)
    await service.retrieve(_CTX, "同一句", source_ids=None, top_n=8)
    await service.retrieve(_CTX, "同一句", source_ids=None, top_n=8)

    assert embedding.calls == 1  # 第二次由 query embedding 快取供應(D9)
