"""Parser 共同規則(PHASE_2 §6「共同規則」+ v1.2 量體上限補遺)。

五個 parser 只負責「格式 → 粗胚 Block」;正規化、空白塊剔除、單塊截斷、全文量體上限
一律收斂在此,NEVER 讓各 parser 各自實作(行為漂移的來源)。
`Block` 為凍結契約(§5,無 meta 欄位),故截斷警示記在 `NormalizedDocument.meta.warnings`。
"""
from typing import Any

from app.domain.entities.document import Block, NormalizedDocument
from app.domain.ports.parser import ParseError

MAX_BLOCK_CHARS = 20_000
MAX_DOCUMENT_CHARS = 2_000_000  # v1.2 補遺:超過即 ParseError(不可重試)

# 全形空白 / NBSP → 半形空白;零寬空白直接移除。一律以 escape 書寫:
# 這些字元在編輯器內不可見,字面量無法目視審查。
_SPACE_EQUIVALENTS = {"　": " ", "\xa0": " ", "​": ""}


def normalize_text(raw: str) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    for src, dst in _SPACE_EQUIVALENTS.items():
        text = text.replace(src, dst)
    return text.strip()


def _normalize_rows(rows: list[list[str]]) -> list[list[str]]:
    normalized = [[normalize_text(cell) for cell in row] for row in rows]
    return [row for row in normalized if any(cell for cell in row)]


def _cap_rows(rows: list[list[str]]) -> tuple[list[list[str]], bool]:
    """表格同樣受單塊上限約束:逐列累加,超過即在列邊界截斷(NEVER 切壞單一列)。"""
    kept: list[list[str]] = []
    total = 0
    for row in rows:
        total += sum(len(cell) for cell in row)
        if total > MAX_BLOCK_CHARS and kept:
            return kept, True
        kept.append(row)
    return kept, False


def _block_chars(block: Block) -> int:
    if block.rows is not None:
        return sum(len(cell) for row in block.rows for cell in row)
    return len(block.text)


def finalize_document(blocks: list[Block], meta: dict[str, Any]) -> NormalizedDocument:
    """套用共同規則後產出 NormalizedDocument。

    - 文字正規化;strip 後為空的 block 丟棄(表格則丟棄全空的列)
    - 單一 block 超過 MAX_BLOCK_CHARS 截斷,並於 meta.warnings 留痕(NEVER 靜默丟棄)
    - 全文超過 MAX_DOCUMENT_CHARS → ParseError
    """
    kept: list[Block] = []
    warnings: list[str] = []
    total = 0

    for block in blocks:
        if block.rows is not None:
            rows = _normalize_rows(block.rows)
            if not rows:
                continue
            rows, truncated = _cap_rows(rows)
            if truncated:
                warnings.append(f"table_truncated:{len(kept)}")
            block = block.model_copy(update={"rows": rows, "text": ""})
        else:
            text = normalize_text(block.text)
            if not text:
                continue
            if len(text) > MAX_BLOCK_CHARS:
                text = text[:MAX_BLOCK_CHARS]
                warnings.append(f"block_truncated:{len(kept)}")
            block = block.model_copy(update={"text": text})

        total += _block_chars(block)
        if total > MAX_DOCUMENT_CHARS:
            raise ParseError(f"文件內容超過 {MAX_DOCUMENT_CHARS} 字上限")
        kept.append(block)

    doc_meta = dict(meta)
    if warnings:
        doc_meta["warnings"] = warnings
    return NormalizedDocument(blocks=kept, meta=doc_meta)
