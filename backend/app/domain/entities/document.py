"""文件解析的中間表示(凍結契約;PHASE_2 §5)。

`NormalizedDocument` 是 parser 與 chunking 之間的唯一介面:任何格式(pdf / docx /
html / txt / md)解析後都收斂成同一組 `Block`,chunking 因此完全不知道原始格式。
本檔為純 domain,NEVER import 任何框架 / SDK(§C.2)。
"""
from typing import Any, Literal

from pydantic import BaseModel, Field

BlockType = Literal["heading", "paragraph", "list_item", "table", "code"]


class Block(BaseModel):
    type: BlockType
    text: str = ""  # table 時為空,內容在 rows
    level: int | None = None  # heading 層級 1..6
    page: int | None = None
    rows: list[list[str]] | None = None  # table 專用,rows[0] 視為表頭


class NormalizedDocument(BaseModel):
    blocks: list[Block]
    meta: dict[str, Any] = Field(default_factory=dict)  # {source_mime, page_count, ...}
