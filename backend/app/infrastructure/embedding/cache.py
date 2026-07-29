"""Query embedding 快取(T2.4;PHASE_2 §9.1 D9)。

**只快取 query embedding**(TTL 1h):同一問句重打 embedding API 是可省的重複成本,
且 query 無失效問題。chunk embedding 的 30 天快取已於 v1.2 砍除(跨文件命中≈0、
最壞撐爆 cache 實例),NEVER 在此重新引入。

key = `emb:q:{version}:{sha256(query)}`;version 入 key,換模型自動失效。
vector 以 JSON 陣列存放。Redis 不可用不應阻斷檢索——get 失敗回 None(視為 miss)、
set 失敗只 log(呼叫端已有向量)。
"""
import hashlib
import json

import structlog
from redis.asyncio import Redis

from app.domain.ports.embedding import EmbeddingProvider

_logger = structlog.get_logger()


class QueryEmbeddingCache:
    def __init__(self, redis: Redis, *, version: str, ttl_s: int) -> None:
        self._redis = redis
        self._version = version
        self._ttl_s = ttl_s

    def _key(self, query: str) -> str:
        digest = hashlib.sha256(query.encode("utf-8")).hexdigest()
        return f"emb:q:{self._version}:{digest}"

    async def get(self, query: str) -> list[float] | None:
        try:
            raw = await self._redis.get(self._key(query))
        except Exception:
            _logger.warning("query_embedding_cache_get_failed", exc_info=True)
            return None
        if raw is None:
            return None
        try:
            return [float(x) for x in json.loads(raw)]
        except (ValueError, TypeError):
            return None  # 壞快取視為 miss

    async def set(self, query: str, vector: list[float]) -> None:
        try:
            await self._redis.set(self._key(query), json.dumps(vector), ex=self._ttl_s)
        except Exception:
            _logger.warning("query_embedding_cache_set_failed", exc_info=True)


async def embed_query(
    provider: EmbeddingProvider, cache: QueryEmbeddingCache, text: str
) -> list[float]:
    """cache-aside 取得單一 query 向量;命中不打 API(§9.1)。T2.6 檢索由此取 qvec。"""
    cached = await cache.get(text)
    if cached is not None:
        return cached
    [vector] = await provider.embed([text])
    await cache.set(text, vector)
    return vector
