"""default_recursive chunking(PHASE_2 §7)。

規則(依序):
1. 走訪 blocks 並維護 heading_path(heading 依 level 進出堆疊);heading **自身不單獨成
   chunk**,而是併入後續內容的開頭——單獨一行標題的向量幾乎檢索不到,附在內容前才有用。
2. 連續 paragraph / list_item / code 累積至 target_tokens 封一個 chunk;**跨 heading 邊界
   強制切分**(不同章節的內容 NEVER 混進同一個 chunk)。
3. 單一超長 block:依句號 / 換行遞迴二分至 ≤ target。
4. 相鄰 chunk 依 overlap_tokens 取前塊尾端重疊(同一 section 內才重疊)。
5. table block 委派 table_row(§7-5)。
6. meta 含 {page, heading_path, block_type, kind}(R6:`kind="document"` 自 P2 寫入)。
"""
import re

from app.domain.entities.chunk import ChunkDraft
from app.domain.entities.document import Block, NormalizedDocument
from app.domain.ports.chunking import ChunkingConfig
from app.infrastructure.parsing.chunking.table_row import pack_table
from app.infrastructure.parsing.chunking.tokens import estimate_tokens

_TEXT_BLOCK_TYPES = frozenset({"paragraph", "list_item", "code"})
# 遞迴二分的切點:中文句末標點、英文句點後空白、換行。
_SPLIT_POINTS = re.compile(r"(?<=[。!?！?;;\n])|(?<=\.\s)")
_KIND = "document"


def _split_to_limit(text: str, target_tokens: int) -> list[str]:
    """遞迴二分至每段 ≤ target_tokens;找不到語意切點時退化為字元中分。"""
    if estimate_tokens(text) <= target_tokens or len(text) <= 1:
        return [text]

    middle = len(text) // 2
    candidates = [m.start() for m in _SPLIT_POINTS.finditer(text) if 0 < m.start() < len(text)]
    cut = min(candidates, key=lambda pos: abs(pos - middle)) if candidates else middle
    return _split_to_limit(text[:cut], target_tokens) + _split_to_limit(text[cut:], target_tokens)


def _tail_within(text: str, overlap_tokens: int) -> str:
    """取尾端不超過 overlap_tokens 的完整句子,作為下一塊的重疊前綴。"""
    if overlap_tokens <= 0:
        return ""
    pieces = [p for p in _SPLIT_POINTS.split(text) if p and p.strip()]
    tail: list[str] = []
    tokens = 0
    for piece in reversed(pieces):
        piece_tokens = estimate_tokens(piece)
        if tokens + piece_tokens > overlap_tokens:
            break  # 單句就超過預算 → 不重疊,NEVER 讓 overlap 撐大到接近 target
        tail.insert(0, piece)
        tokens += piece_tokens
    return "".join(tail).strip()


class _Builder:
    def __init__(self, cfg: ChunkingConfig) -> None:
        self._cfg = cfg
        self._drafts: list[ChunkDraft] = []
        self._headings: list[tuple[int, str]] = []
        self._pending: list[str] = []
        self._pending_tokens = 0
        self._pending_page: int | None = None
        self._section_prefix: str | None = None
        self._overlap: str = ""

    # --- blocks ----------------------------------------------------------

    def start_section(self, block: Block) -> None:
        self.flush()
        level = block.level or 1
        while self._headings and self._headings[-1][0] >= level:
            self._headings.pop()
        self._headings.append((level, block.text))
        self._section_prefix = block.text
        self._overlap = ""  # 跨 heading NEVER 重疊

    def add_text(self, block: Block) -> None:
        for piece in _split_to_limit(block.text, self._cfg.target_tokens):
            piece_tokens = estimate_tokens(piece)
            if self._pending and self._pending_tokens + piece_tokens > self._cfg.target_tokens:
                self.flush()
            if not self._pending:
                self._pending_page = block.page
            self._pending.append(piece)
            self._pending_tokens += piece_tokens

    def add_table(self, block: Block) -> None:
        self.flush()  # 表格自成 chunk,不與前文混合
        for text in pack_table(block, self._cfg):
            self._emit(text, block_type="table", page=block.page)
        self._overlap = ""

    # --- 產出 ------------------------------------------------------------

    def flush(self) -> None:
        if not self._pending:
            return
        body = "\n".join(self._pending)
        parts = [part for part in (self._overlap, self._section_prefix, body) if part]
        self._emit("\n".join(parts), block_type="text", page=self._pending_page)
        self._section_prefix = None
        self._overlap = _tail_within(body, self._cfg.overlap_tokens)
        self._pending = []
        self._pending_tokens = 0
        self._pending_page = None

    def _emit(self, text: str, *, block_type: str, page: int | None) -> None:
        self._drafts.append(
            ChunkDraft(
                seq=len(self._drafts),
                text=text,
                tokens=estimate_tokens(text),
                meta={
                    "page": page,
                    "heading_path": [title for _, title in self._headings],
                    "block_type": block_type,
                    "kind": _KIND,
                },
            )
        )

    def finish(self) -> list[ChunkDraft]:
        self.flush()
        return self._drafts


class DefaultRecursiveChunking:
    name = "default_recursive"

    def chunk(self, doc: NormalizedDocument, cfg: ChunkingConfig) -> list[ChunkDraft]:
        builder = _Builder(cfg)
        for block in doc.blocks:
            if block.type == "heading":
                builder.start_section(block)
            elif block.type == "table":
                builder.add_table(block)
            elif block.type in _TEXT_BLOCK_TYPES:
                builder.add_text(block)
        return builder.finish()
