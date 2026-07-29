"""QueryEmbeddingCache 與 embed_query 測試(T2.4;PHASE_2 §9.1 D9)。

以 in-memory FakeRedis 取代真實 Redis(單元測試不碰外部服務);驗證「快取命中不打 API」。
"""
import json

import pytest

from app.infrastructure.embedding.cache import QueryEmbeddingCache, embed_query
from tests.fakes import FakeEmbeddingProvider

pytestmark = pytest.mark.anyio


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.set_calls: list[tuple[str, int | None]] = []

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value
        self.set_calls.append((key, ex))


class BrokenRedis:
    async def get(self, key: str) -> str | None:
        raise ConnectionError("redis down")

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        raise ConnectionError("redis down")


def _cache(redis: object, *, ttl_s: int = 3600) -> QueryEmbeddingCache:
    return QueryEmbeddingCache(redis, version="bge-m3@1024", ttl_s=ttl_s)  # type: ignore[arg-type]


async def test_get_miss_returns_none() -> None:
    assert await _cache(FakeRedis()).get("沒存過") is None


async def test_set_then_get_roundtrip() -> None:
    cache = _cache(FakeRedis())
    await cache.set("問句", [0.1, 0.2, 0.3])
    assert await cache.get("問句") == [0.1, 0.2, 0.3]


async def test_set_uses_configured_ttl() -> None:
    redis = FakeRedis()
    await _cache(redis, ttl_s=1800).set("問句", [0.1])
    assert redis.set_calls[0][1] == 1800


async def test_key_includes_version_so_model_switch_invalidates() -> None:
    redis = FakeRedis()
    await QueryEmbeddingCache(redis, version="v1", ttl_s=60).set("問句", [1.0])  # type: ignore[arg-type]
    # 換 version → 不同 key → miss
    assert await QueryEmbeddingCache(redis, version="v2", ttl_s=60).get("問句") is None  # type: ignore[arg-type]


async def test_corrupt_cache_value_is_treated_as_miss() -> None:
    redis = FakeRedis()
    cache = _cache(redis)
    redis.store[cache._key("問句")] = "not-json"
    assert await cache.get("問句") is None


async def test_get_survives_redis_failure() -> None:
    assert await _cache(BrokenRedis()).get("問句") is None


async def test_set_survives_redis_failure() -> None:
    await _cache(BrokenRedis()).set("問句", [1.0])  # NEVER 拋出


# --- embed_query cache-aside(§9.1)------------------------------------------

async def test_embed_query_caches_and_skips_api_on_hit() -> None:
    provider = FakeEmbeddingProvider()
    cache = _cache(FakeRedis())

    first = await embed_query(provider, cache, "台灣的首都")
    second = await embed_query(provider, cache, "台灣的首都")

    assert first == second
    assert provider.calls == 1  # 快取命中不打 API


async def test_embed_query_different_queries_each_call_api() -> None:
    provider = FakeEmbeddingProvider()
    cache = _cache(FakeRedis())

    await embed_query(provider, cache, "問句一")
    await embed_query(provider, cache, "問句二")

    assert provider.calls == 2


async def test_embed_query_stores_json_vector() -> None:
    provider = FakeEmbeddingProvider()
    redis = FakeRedis()
    vector = await embed_query(provider, _cache(redis), "問句")
    (stored,) = redis.store.values()
    assert json.loads(stored) == vector
