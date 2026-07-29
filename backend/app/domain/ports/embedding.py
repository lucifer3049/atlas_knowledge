"""`EmbeddingProvider` port 與 `EmbeddingError`(凍結契約;PHASE_2 §5)。

一個 OpenAI-compatible adapter 同時覆蓋本地(Ollama bge-m3)與商用 API(D13);
換模型 = 換 adapter 設定 + 新 `embedding_version`,application 層不動。
錯誤沿用 provider 五類分類(`ProviderErrorCode`,與 LLM port 共用),
維度不符等契約違反歸 `provider_error`。本檔為純 domain,NEVER import 框架 / SDK(§C.2)。
"""
from collections.abc import Sequence
from typing import Protocol

from app.domain.ports.llm import ProviderErrorCode


class EmbeddingError(Exception):
    """embedding 供應商錯誤;`code` 沿用 provider 五類(§6)。"""

    def __init__(self, code: ProviderErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class EmbeddingProvider(Protocol):
    version: str  # 例:"bge-m3@1024";寫入 document_chunks.embedding_version
    dim: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """回傳與輸入等長、順序一致的向量清單;失敗 raise EmbeddingError。"""
        ...
