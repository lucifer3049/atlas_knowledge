"""DOCX parser(python-docx;PHASE_2 §6)。

段落與表格 MUST 依原文順序輸出(python-docx 的 `paragraphs` / `tables` 是兩份分離清單,
直接用會打亂順序),故走 body XML 逐一走訪。
解析前檢查 zip 解壓總量(v1.2 補遺:> 200MB → ParseError,防解壓炸彈)。
"""
import io
import zipfile

from docx import Document as open_docx
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.domain.entities.document import Block, NormalizedDocument
from app.domain.ports.parser import ParseError
from app.infrastructure.parsing.blocks import finalize_document

MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
_LIST_STYLE_PREFIXES = ("List", "清單", "項目符號")
_MAX_HEADING_LEVEL = 6


def _guard_zip_bomb(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            total = sum(info.file_size for info in archive.infolist())
    except zipfile.BadZipFile as exc:
        raise ParseError("DOCX 檔案損毀") from exc
    if total > MAX_UNCOMPRESSED_BYTES:
        raise ParseError("DOCX 解壓後體積超過上限")


def _heading_level(style_name: str) -> int | None:
    if not style_name.startswith("Heading "):
        return None
    try:
        level = int(style_name.removeprefix("Heading ").strip())
    except ValueError:
        return None
    return min(level, _MAX_HEADING_LEVEL)


def _paragraph_block(paragraph: Paragraph) -> Block:
    style_name = (paragraph.style.name if paragraph.style is not None else "") or ""
    level = _heading_level(style_name)
    if level is not None:
        return Block(type="heading", text=paragraph.text, level=level)
    if style_name.startswith(_LIST_STYLE_PREFIXES):
        return Block(type="list_item", text=paragraph.text)
    return Block(type="paragraph", text=paragraph.text)


class DocxParser:
    media_types: tuple[str, ...] = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    def parse(self, data: bytes, filename: str) -> NormalizedDocument:
        _guard_zip_bomb(data)
        try:
            document = open_docx(io.BytesIO(data))
        except Exception as exc:  # python-docx 對壞檔拋多種例外,一律視為不可重試
            raise ParseError("DOCX 無法解析") from exc

        blocks: list[Block] = []
        for child in document.element.body.iterchildren():
            if child.tag == qn("w:p"):
                blocks.append(_paragraph_block(Paragraph(child, document)))
            elif child.tag == qn("w:tbl"):
                table = Table(child, document)
                rows = [[cell.text for cell in row.cells] for row in table.rows]
                blocks.append(Block(type="table", rows=rows))

        return finalize_document(blocks, {"source_mime": self.media_types[0]})
