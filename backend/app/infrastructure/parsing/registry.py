"""Parser registry(PHASE_2 §6):canonical mime → DocumentParser。

不支援的型別在**上傳時**即 415(§11.2 白名單),NEVER 等進 pipeline 才失敗;
因此走到這裡卻查無 parser = 白名單與 registry 不同步的組態錯誤,以 ParseError
(不可重試)呈現,讓文件標 failed 並留下可讀訊息。
"""
from app.domain.ports.parser import DocumentParser, ParseError
from app.infrastructure.parsing.docx import DocxParser
from app.infrastructure.parsing.html import HtmlParser
from app.infrastructure.parsing.pdf import PdfParser
from app.infrastructure.parsing.text import MarkdownParser, PlainTextParser

_PARSERS: tuple[DocumentParser, ...] = (
    PdfParser(),
    DocxParser(),
    HtmlParser(),
    PlainTextParser(),
    MarkdownParser(),
)

_BY_MEDIA_TYPE: dict[str, DocumentParser] = {
    media_type: parser for parser in _PARSERS for media_type in parser.media_types
}


def get_parser(mime: str) -> DocumentParser:
    parser = _BY_MEDIA_TYPE.get(mime)
    if parser is None:
        raise ParseError(f"沒有對應 {mime} 的 parser")
    return parser


def supported_media_types() -> frozenset[str]:
    return frozenset(_BY_MEDIA_TYPE)
