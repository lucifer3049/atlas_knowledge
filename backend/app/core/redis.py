"""Redis client 工廠(T2.4;§F.10)。

query embedding 快取 / rate limit / 配額計數(P7/P8)共用 cache 實例。P2 只有 embedding
快取。`decode_responses=False`:快取值為 JSON bytes,由呼叫端自行 decode。
正式環境 broker 與 cache 為兩個實例(§F.10),P2 開發沿用單一 redis_url。
"""
from redis.asyncio import Redis

from app.core.config import Settings


def create_redis_client(settings: Settings) -> Redis:
    client: Redis = Redis.from_url(settings.redis_url)
    return client
