"""`ChunkingStrategy` port 與 `ChunkingConfig`(凍結契約;PHASE_2 §5、§7)。

策略可換是第一級需求:P2 只有 default_recursive(+ table_row 委派),
P3 的 law_hierarchical 由 registry 插入,呼叫端不改一行。
本檔為純 domain,NEVER import 任何框架 / SDK(§C.2)——組態值由呼叫端自 settings 傳入。
"""
from typing import Protocol

from pydantic import BaseModel

from app.domain.entities.chunk import ChunkDraft
from app.domain.entities.document import NormalizedDocument


class ChunkingConfig(BaseModel):
    target_tokens: int = 450
    overlap_tokens: int = 80


class ChunkingStrategy(Protocol):
    name: str

    def chunk(self, doc: NormalizedDocument, cfg: ChunkingConfig) -> list[ChunkDraft]: ...
