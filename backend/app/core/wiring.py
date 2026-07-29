"""共用組裝(API deps 與 worker 共用;§C.2、PHASE_1 v1.2 §22)。

adapter 組裝邏輯集中於此,NEVER 在 deps 與 worker 各複製一份。
"""
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.retrieval_service import RetrievalService
from app.core.config import Settings
from app.core.model_registry import default_alias, resolve
from app.domain.ports.embedding import EmbeddingProvider
from app.domain.ports.llm import LLMProvider
from app.domain.ports.reranker import NoopReranker
from app.infrastructure.embedding.cache import QueryEmbeddingCache
from app.infrastructure.embedding.openai_compat import OpenAICompatEmbedding
from app.infrastructure.llm.openai_compat import OpenAICompatProvider
from app.infrastructure.vector.pgvector_index import PgVectorChunkIndex


def build_llm(settings: Settings) -> LLMProvider:
    """由 default alias 組 LLM adapter(§R R2)。連線層(base_url/api_key/timeout)取自
    settings;model 名於呼叫端依 conversation.model_alias 解析。多 provider 為 P6 ModelRouter。"""
    resolved = resolve(default_alias())
    if resolved.provider != "openai_compat":
        raise RuntimeError(f"P1 僅支援 openai_compat provider,取得 {resolved.provider!r}")
    return OpenAICompatProvider(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        timeout_s=settings.llm_timeout_s,
    )


def build_embedding(settings: Settings) -> EmbeddingProvider:
    """組 embedding adapter(T2.4;§5、D13)。開發與商用 API 共用同一 OpenAI-compatible
    adapter,差異全在 settings。worker(embed 階段)與 T2.6 檢索共用此組裝。"""
    return OpenAICompatEmbedding(
        base_url=settings.embedding_base_url,
        api_key=settings.embedding_api_key,
        model=settings.embedding_model,
        version=settings.embedding_version,
        dim=settings.embedding_dim,
        timeout_s=settings.embedding_timeout_s,
        batch_size=settings.embedding_batch_size,
    )


def build_retrieval(
    settings: Settings,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    embedding: EmbeddingProvider,
    redis: Redis,
) -> RetrievalService:
    """組 RetrievalService(T2.6;§9.1)。rerank 於 P2 只有 Noop(§0-4)。"""
    return RetrievalService(
        chunk_index=PgVectorChunkIndex(
            session_factory,
            top_k=settings.retrieval_top_k,
            rrf_k=settings.rrf_k,
            ef_search=settings.hnsw_ef_search,
        ),
        embedding=embedding,
        cache=QueryEmbeddingCache(
            redis,
            version=settings.embedding_version,
            ttl_s=settings.embedding_query_cache_ttl_s,
        ),
        reranker=NoopReranker(),
    )
