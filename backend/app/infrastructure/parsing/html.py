"""HTML parser(bs4 + lxml;PHASE_2 §6、D10)。

上傳的 HTML 是使用者自有檔案,不需正文抽取器(trafilatura 延至 P5 的網頁抓取情境)。
只取結構性元素:h1–h6 / p / li / table;script、style 先行移除。
"""
from bs4 import BeautifulSoup, Tag

from app.domain.entities.document import Block, NormalizedDocument
from app.infrastructure.parsing.blocks import finalize_document
from app.infrastructure.parsing.text import decode_text

_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_WANTED = [*_HEADINGS, "p", "li", "table"]


def _table_rows(table: Tag) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        if not isinstance(tr, Tag):
            continue
        cells = [
            cell.get_text(" ", strip=True)
            for cell in tr.find_all(["th", "td"])
            if isinstance(cell, Tag)
        ]
        if cells:
            rows.append(cells)
    return rows


class HtmlParser:
    media_types: tuple[str, ...] = ("text/html",)

    def parse(self, data: bytes, filename: str) -> NormalizedDocument:
        soup = BeautifulSoup(decode_text(data), "lxml")
        for tag in soup.find_all(["script", "style"]):
            tag.decompose()

        blocks: list[Block] = []
        for el in soup.find_all(_WANTED):
            if not isinstance(el, Tag):
                continue
            # 表格整塊處理,故略過巢狀於表格內的 p / li,避免同一段文字重複入庫。
            if el.name != "table" and el.find_parent("table") is not None:
                continue
            if el.name == "table":
                blocks.append(Block(type="table", rows=_table_rows(el)))
            elif el.name in _HEADINGS:
                blocks.append(
                    Block(
                        type="heading",
                        text=el.get_text(" ", strip=True),
                        level=_HEADINGS[el.name],
                    )
                )
            elif el.name == "li":
                blocks.append(Block(type="list_item", text=el.get_text(" ", strip=True)))
            elif el.find_parent("li") is None:  # li 內的 p 由 li 自身涵蓋
                blocks.append(Block(type="paragraph", text=el.get_text(" ", strip=True)))

        return finalize_document(blocks, {"source_mime": "text/html"})
