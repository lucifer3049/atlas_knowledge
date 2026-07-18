"""PDF parser(PyMuPDF;PHASE_2 §6)。

逐頁取 block 級文字。heading 判定為**啟發式**:以全文最常見字級為 body,字級明顯大於
body(或粗體且略大)且長度短者視為 heading;可信度低一律降級 paragraph
(§6:寧可少判 heading,NEVER 把內文誤切成標題)。
加密 / 零頁 / 損毀 → ParseError(不可重試)。
"""
from collections import Counter
from typing import Any

import pymupdf

from app.domain.entities.document import Block, NormalizedDocument
from app.domain.ports.parser import ParseError
from app.infrastructure.parsing.blocks import finalize_document

_BOLD_FLAG = 1 << 4
_HEADING_MAX_CHARS = 80  # 超過此長度一律視為內文
_H1_RATIO = 1.5
_H2_RATIO = 1.25
_BOLD_RATIO = 1.05


def _block_text_and_style(block: dict[str, Any]) -> tuple[str, float, bool]:
    """回傳 (文字, 最大字級, 是否整塊粗體)。"""
    parts: list[str] = []
    max_size = 0.0
    spans = 0
    bold_spans = 0
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            parts.append(span.get("text", ""))
            max_size = max(max_size, float(span.get("size", 0)))
            spans += 1
            if int(span.get("flags", 0)) & _BOLD_FLAG:
                bold_spans += 1
        parts.append("\n")
    return "".join(parts), max_size, spans > 0 and bold_spans == spans


def _heading_level(size: float, bold: bool, body_size: float, text: str) -> int | None:
    if body_size <= 0 or len(text) > _HEADING_MAX_CHARS:
        return None
    ratio = size / body_size
    if ratio >= _H1_RATIO:
        return 1
    if ratio >= _H2_RATIO:
        return 2
    if bold and ratio >= _BOLD_RATIO:
        return 3
    return None


class PdfParser:
    media_types: tuple[str, ...] = ("application/pdf",)

    def parse(self, data: bytes, filename: str) -> NormalizedDocument:
        try:
            document = pymupdf.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise ParseError("PDF 無法開啟") from exc

        with document:
            if document.needs_pass:
                raise ParseError("PDF 已加密,無法解析")
            if document.page_count == 0:
                raise ParseError("PDF 沒有任何頁面")

            pages: list[list[tuple[str, float, bool, int]]] = []
            sizes: Counter[int] = Counter()
            for index in range(document.page_count):
                page = document.load_page(index)
                page_number = index + 1
                page_blocks: list[tuple[str, float, bool, int]] = []
                for raw in page.get_text("dict").get("blocks", []):
                    if raw.get("type") != 0:  # 影像等非文字 block(OCR 為 backlog)
                        continue
                    text, size, bold = _block_text_and_style(raw)
                    if not text.strip():
                        continue
                    page_blocks.append((text, size, bold, page_number))
                    sizes[round(size)] += len(text)
                pages.append(page_blocks)
            page_count = document.page_count

        body_size = float(sizes.most_common(1)[0][0]) if sizes else 0.0
        blocks: list[Block] = []
        for page_blocks in pages:
            for text, size, bold, page_number in page_blocks:
                level = _heading_level(size, bold, body_size, text.strip())
                blocks.append(
                    Block(
                        type="heading" if level is not None else "paragraph",
                        text=text,
                        level=level,
                        page=page_number,
                    )
                )

        return finalize_document(
            blocks, {"source_mime": "application/pdf", "page_count": page_count}
        )
