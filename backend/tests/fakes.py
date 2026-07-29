"""跨測試共用的 fake adapters(PHASE_2 §12.1)。

`FakeEmbeddingProvider` 為檢索/嵌入測試的決定性基準:相同文字必得相同單位向量,
故「查詢與目標 chunk 同文字時必為 top1」可被斷言(T2.6 檢索測試依賴此性質)。
維度預設對齊 `EMBEDDING_DIM`(= DB `vector(n)` 欄位),否則落庫會被 pgvector 拒絕。
"""
import hashlib
import math
from collections.abc import Sequence

from app.infrastructure.db.models import EMBEDDING_DIM


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
