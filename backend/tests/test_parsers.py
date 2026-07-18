"""T2.2 parser 測試(純單元;PHASE_2 §12.1、§14 T2.2 測試清單)。

樣本檔位於 tests/fixtures/docs/(由同目錄 make_fixtures.py 產生)。
"""
from pathlib import Path

import pytest

from app.domain.entities.document import Block, NormalizedDocument
from app.domain.ports.parser import ParseError
from app.infrastructure.parsing import blocks as blocks_module
from app.infrastructure.parsing import docx as docx_module
from app.infrastructure.parsing.blocks import (
    MAX_BLOCK_CHARS,
    finalize_document,
    normalize_text,
)
from app.infrastructure.parsing.registry import get_parser, supported_media_types
from app.infrastructure.parsing.text import decode_text

_DOCS = Path(__file__).parent / "fixtures" / "docs"
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _parse(name: str, mime: str) -> NormalizedDocument:
    return get_parser(mime).parse((_DOCS / name).read_bytes(), name)


# --- registry ---------------------------------------------------------------

def test_registry_covers_upload_whitelist() -> None:
    # 與 §11.2 上傳白名單的 canonical mime 一一對應,NEVER 讓上傳放行卻無 parser。
    assert supported_media_types() == {
        "application/pdf",
        _DOCX_MIME,
        "text/plain",
        "text/markdown",
        "text/html",
    }


def test_unknown_mime_raises_parse_error() -> None:
    with pytest.raises(ParseError):
        get_parser("application/x-nope")


# --- txt / md ---------------------------------------------------------------

def test_plain_text_splits_on_blank_lines() -> None:
    doc = _parse("sample.txt", "text/plain")
    assert [b.type for b in doc.blocks] == ["paragraph"] * 3
    # 連續空行只切一次:空白段落被共同規則剔除
    assert doc.blocks[1].text == "第二段落。"
    assert doc.meta["source_mime"] == "text/plain"


def test_plain_text_normalizes_full_width_space() -> None:
    doc = _parse("sample.txt", "text/plain")
    assert "　" not in doc.blocks[0].text
    assert "全形空白 與一般空白" in doc.blocks[0].text


def test_big5_file_is_decoded_via_encoding_chain() -> None:
    doc = _parse("sample_big5.txt", "text/plain")
    assert doc.blocks[0].text.startswith("繁體中文")


def test_undecodable_bytes_raise_parse_error() -> None:
    with pytest.raises(ParseError):
        decode_text(b"\xff\xfe\x00\x80\x81\x8d\x8f")


def test_markdown_headings_code_and_paragraphs() -> None:
    doc = _parse("sample.md", "text/markdown")
    assert [(b.type, b.level) for b in doc.blocks] == [
        ("heading", 1),
        ("paragraph", None),
        ("heading", 2),
        ("paragraph", None),  # 清單於 md 視同段落(§6:其餘同 txt)
        ("code", None),
        ("paragraph", None),
    ]
    assert doc.blocks[0].text == "標題一"
    assert doc.blocks[4].text == 'print("hello")'


def test_markdown_unclosed_fence_keeps_content() -> None:
    parser = get_parser("text/markdown")
    doc = parser.parse(b"# t\n\n```\nno close\n", "a.md")
    assert doc.blocks[-1].type == "code"
    assert doc.blocks[-1].text == "no close"


# --- html -------------------------------------------------------------------

def test_html_structure_and_noise_removal() -> None:
    doc = _parse("sample.html", "text/html")
    assert [(b.type, b.level) for b in doc.blocks] == [
        ("heading", 1),
        ("paragraph", None),
        ("heading", 2),
        ("list_item", None),
        ("list_item", None),
        ("table", None),
    ]
    text = " ".join(b.text for b in doc.blocks)
    assert "console.log" not in text  # script 已移除
    assert "color: red" not in text  # style 已移除


def test_html_table_rows_include_header_and_skip_nested_paragraph() -> None:
    doc = _parse("sample.html", "text/html")
    table = doc.blocks[-1]
    assert table.rows == [["欄位", "數值"], ["甲", "1"], ["乙", "2"]]
    assert table.text == ""


# --- docx -------------------------------------------------------------------

def test_docx_headings_lists_and_table_in_document_order() -> None:
    doc = _parse("sample.docx", _DOCX_MIME)
    assert [(b.type, b.level) for b in doc.blocks] == [
        ("heading", 1),
        ("paragraph", None),
        ("heading", 2),
        ("list_item", None),
        ("list_item", None),
        ("table", None),
    ]
    assert doc.blocks[-1].rows == [["欄位", "數值"], ["甲", "100"]]


def test_docx_corrupt_file_raises_parse_error() -> None:
    with pytest.raises(ParseError):
        get_parser(_DOCX_MIME).parse(b"not a zip at all", "a.docx")


def test_docx_zip_bomb_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docx_module, "MAX_UNCOMPRESSED_BYTES", 10)
    with pytest.raises(ParseError):
        _parse("sample.docx", _DOCX_MIME)


# --- pdf --------------------------------------------------------------------

def test_pdf_blocks_carry_page_and_heading_heuristic() -> None:
    doc = _parse("sample.pdf", "application/pdf")
    assert doc.meta["page_count"] == 1
    assert all(b.page == 1 for b in doc.blocks)
    assert doc.blocks[0].type == "heading"  # 24pt vs 11pt 內文
    assert doc.blocks[0].level == 1
    assert [b.type for b in doc.blocks[1:]] == ["paragraph", "paragraph"]


def test_encrypted_pdf_raises_parse_error() -> None:
    with pytest.raises(ParseError):
        _parse("encrypted.pdf", "application/pdf")


def test_broken_pdf_raises_parse_error() -> None:
    with pytest.raises(ParseError):
        get_parser("application/pdf").parse(b"%PDF-1.4\nbroken", "a.pdf")


# --- 共同規則(§6)-----------------------------------------------------------

def test_normalize_text_collapses_space_equivalents() -> None:
    assert normalize_text("　a\xa0b​c  ") == "a bc"


def test_blank_blocks_are_dropped() -> None:
    doc = finalize_document(
        [
            Block(type="paragraph", text="   \n  "),
            Block(type="paragraph", text="有內容"),
            Block(type="table", rows=[["", " "], ["甲", ""]]),
        ],
        {},
    )
    assert [b.type for b in doc.blocks] == ["paragraph", "table"]
    assert doc.blocks[1].rows == [["甲", ""]]


def test_oversized_block_is_truncated_with_warning() -> None:
    doc = finalize_document([Block(type="paragraph", text="字" * (MAX_BLOCK_CHARS + 500))], {})
    assert len(doc.blocks[0].text) == MAX_BLOCK_CHARS
    assert doc.meta["warnings"] == ["block_truncated:0"]


def test_oversized_table_is_truncated_at_row_boundary() -> None:
    rows = [["字" * 3_000, "字" * 3_000] for _ in range(4)]  # 每列 6,000 字
    doc = finalize_document([Block(type="table", rows=rows)], {})
    assert doc.blocks[0].rows is not None
    assert len(doc.blocks[0].rows) == 3  # 第 4 列會使總量達 24,000 > 20,000
    assert doc.meta["warnings"] == ["table_truncated:0"]


def test_document_over_total_char_limit_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(blocks_module, "MAX_DOCUMENT_CHARS", 100)
    with pytest.raises(ParseError):
        finalize_document([Block(type="paragraph", text="字" * 101)], {})


def test_no_warnings_key_when_nothing_truncated() -> None:
    doc = finalize_document([Block(type="paragraph", text="短")], {"source_mime": "text/plain"})
    assert "warnings" not in doc.meta
