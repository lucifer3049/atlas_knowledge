"""Chunk 相關 domain entity(凍結契約;PHASE_2 §5)。

`ChunkDraft` 是 chunking strategy 的產出、落庫前的中間表示——尚未有 id 與 embedding。
`RetrievedChunk` 是檢索的產出(T2.6):已含 RRF 分數與名次,供 orchestrator 組
context blocks 與 citations(兩者 MUST 為同一份清單,v1.2 §10 補遺)。
本檔為純 domain,NEVER import 任何框架 / SDK(§C.2)。
"""
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ChunkDraft(BaseModel):
    seq: int
    text: str
    tokens: int
    meta: dict[str, Any] = Field(default_factory=dict)  # {page, heading_path, block_type, kind}


class RetrievedChunk(BaseModel):
    chunk_id: UUID
    document_id: UUID
    filename: str
    text: str
    score: float  # RRF 分數
    rank: int  # 1-based
    meta: dict[str, Any] = Field(default_factory=dict)
