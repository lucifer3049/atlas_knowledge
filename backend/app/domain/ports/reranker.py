"""`Reranker` port 與 P2 唯一實作 `NoopReranker`(凍結契約;PHASE_2 §5、§0-4)。

P2 只有 Noop:插槽先留,rerank 實作列 backlog(§1「不做」)。NoopReranker 為零依賴的
純函式行為(原樣回傳前 top_n),故與 port 同置於 domain,不另開 infrastructure 目錄
(§3 目錄樹亦只列 `ports/reranker.py`)。本檔為純 domain,NEVER import 框架 / SDK。
"""
from typing import Protocol

from app.domain.entities.chunk import RetrievedChunk


class Reranker(Protocol):
    async def rerank(
        self, query: str, chunks: list[RetrievedChunk], top_n: int
    ) -> list[RetrievedChunk]: ...


class NoopReranker:
    async def rerank(
        self, query: str, chunks: list[RetrievedChunk], top_n: int
    ) -> list[RetrievedChunk]:
        return chunks[:top_n]
