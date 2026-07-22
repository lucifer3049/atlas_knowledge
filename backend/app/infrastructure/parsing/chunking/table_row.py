"""table_row chunking(PHASE_2 §7-5)。

表格若整塊丟進向量,列與列的語意會互相稀釋;逐列序列化為
「表頭1: 值1｜表頭2: 值2」後,單列即可獨立被檢索命中,且欄名隨值一起進入向量。
`rows[0]` 視為表頭(§5);多列打包至 target_tokens。
"""
from app.domain.entities.chunk import ChunkDraft
from app.domain.entities.document import Block, NormalizedDocument
from app.domain.ports.chunking import ChunkingConfig
from app.infrastructure.parsing.chunking.tokens import estimate_tokens

_CELL_SEPARATOR = "｜"
_ROW_SEPARATOR = "\n"


def _serialize_row(header: list[str], row: list[str]) -> str:
    cells = [
        f"{header[i]}: {value}" if i < len(header) and header[i] else value
        for i, value in enumerate(row)
    ]
    return _CELL_SEPARATOR.join(cell for cell in cells if cell)


def pack_table(block: Block, cfg: ChunkingConfig) -> list[str]:
    """把一個 table block 打包成數段文字,每段即一個 chunk 的內容。"""
    rows = block.rows or []
    if not rows:
        return []
    header, body = rows[0], rows[1:]
    if not body:  # 只有表頭:整列當一般內容,NEVER 靜默丟棄
        return [_CELL_SEPARATOR.join(cell for cell in header if cell)]

    packed: list[str] = []
    buffer: list[str] = []
    buffer_tokens = 0
    for row in body:
        line = _serialize_row(header, row)
        if not line:
            continue
        line_tokens = estimate_tokens(line)
        if buffer and buffer_tokens + line_tokens > cfg.target_tokens:
            packed.append(_ROW_SEPARATOR.join(buffer))
            buffer, buffer_tokens = [], 0
        buffer.append(line)
        buffer_tokens += line_tokens
    if buffer:
        packed.append(_ROW_SEPARATOR.join(buffer))
    return packed


class TableRowChunking:
    """port 形式的入口(只處理 table block);一般文件由 default_recursive 委派進來。"""

    name = "table_row"

    def chunk(self, doc: NormalizedDocument, cfg: ChunkingConfig) -> list[ChunkDraft]:
        drafts: list[ChunkDraft] = []
        for block in doc.blocks:
            if block.type != "table":
                continue
            for text in pack_table(block, cfg):
                drafts.append(
                    ChunkDraft(
                        seq=len(drafts),
                        text=text,
                        tokens=estimate_tokens(text),
                        meta={"block_type": "table", "kind": "document", "page": block.page},
                    )
                )
        return drafts
