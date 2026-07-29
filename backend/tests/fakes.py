"""跨測試共用的 fake adapters(PHASE_2 §12.1)。

`FakeEmbeddingProvider` 為檢索/嵌入測試的決定性基準:相同文字必得相同單位向量,
故「查詢與目標 chunk 同文字時必為 top1」可被斷言(T2.6 檢索測試依賴此性質)。
"""
import hashlib
import math
from collections.abc import Sequence


class FakeEmbeddingProvider:
    version = "fake@8"
    dim = 8

    def __init__(self) -> None:
        self.calls = 0
        self.embedded_texts: list[str] = []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        self.embedded_texts.extend(texts)
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [digest[i] + 1 for i in range(self.dim)]  # +1 避免全零向量
        norm = math.sqrt(sum(x * x for x in raw))
        return [x / norm for x in raw]
