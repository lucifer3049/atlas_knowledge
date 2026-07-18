"""純文字與 Markdown parser(PHASE_2 §6)。

Markdown **NEVER 引入 markdown 套件**(§F.5):只認 ATX 標題與圍欄 code block,
其餘與 txt 同樣以空行分段——RAG 需要的是段落邊界,不是 HTML 渲染。
編碼鏈 utf-8-sig → utf-8 → cp950(v1.2 補遺:繁中 Big5/cp950 檔在台灣仍常見),
皆失敗 → ParseError(不可重試)。
"""
import re

from app.domain.entities.document import Block, NormalizedDocument
from app.domain.ports.parser import ParseError
from app.infrastructure.parsing.blocks import finalize_document

_ENCODING_CHAIN = ("utf-8-sig", "utf-8", "cp950")
_PARAGRAPH_SPLIT = re.compile(r"\n[ \t]*\n")
_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE = "```"


def decode_text(data: bytes) -> str:
    for encoding in _ENCODING_CHAIN:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ParseError(f"無法以 {'/'.join(_ENCODING_CHAIN)} 解碼文字檔")


def _paragraph_blocks(text: str) -> list[Block]:
    return [Block(type="paragraph", text=part) for part in _PARAGRAPH_SPLIT.split(text)]


class PlainTextParser:
    media_types: tuple[str, ...] = ("text/plain",)

    def parse(self, data: bytes, filename: str) -> NormalizedDocument:
        text = decode_text(data)
        return finalize_document(_paragraph_blocks(text), {"source_mime": "text/plain"})


class MarkdownParser:
    media_types: tuple[str, ...] = ("text/markdown",)

    def parse(self, data: bytes, filename: str) -> NormalizedDocument:
        text = decode_text(data).replace("\r\n", "\n").replace("\r", "\n")
        blocks: list[Block] = []
        buffer: list[str] = []
        fence_lines: list[str] | None = None

        def flush_paragraph() -> None:
            if buffer:
                blocks.extend(_paragraph_blocks("\n".join(buffer)))
                buffer.clear()

        for line in text.split("\n"):
            if line.strip().startswith(_FENCE):
                if fence_lines is None:  # 圍欄開始
                    flush_paragraph()
                    fence_lines = []
                else:  # 圍欄結束
                    blocks.append(Block(type="code", text="\n".join(fence_lines)))
                    fence_lines = None
                continue
            if fence_lines is not None:
                fence_lines.append(line)
                continue

            heading = _ATX_HEADING.match(line)
            if heading is not None:
                flush_paragraph()
                blocks.append(
                    Block(type="heading", text=heading.group(2), level=len(heading.group(1)))
                )
                continue
            if not line.strip():
                flush_paragraph()
                continue
            buffer.append(line)

        if fence_lines is not None:  # 未閉合的圍欄:內容照收,NEVER 靜默丟棄
            blocks.append(Block(type="code", text="\n".join(fence_lines)))
        flush_paragraph()
        return finalize_document(blocks, {"source_mime": "text/markdown"})
