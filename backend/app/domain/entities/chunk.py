"""Chunk 相關 domain entity(凍結契約;PHASE_2 §5)。

`ChunkDraft` 是 chunking strategy 的產出、落庫前的中間表示——尚未有 id 與 embedding。
本檔為純 domain,NEVER import 任何框架 / SDK(§C.2)。
"""
from typing import Any

from pydantic import BaseModel, Field


class ChunkDraft(BaseModel):
    seq: int
    text: str
    tokens: int
    meta: dict[str, Any] = Field(default_factory=dict)  # {page, heading_path, block_type, kind}
