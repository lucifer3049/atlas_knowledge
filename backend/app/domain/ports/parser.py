"""`DocumentParser` port 與 `ParseError`(凍結契約;PHASE_2 §5)。

parse 為 **sync**:只在 worker 內執行(CPU-bound,NEVER 出現在 async 請求路徑)。
`ParseError` 為**不可重試型錯誤**(§8.2):壞檔重跑一百次還是壞檔,任務收到即直接
把 document 標為 failed,NEVER 進退避重試。I/O 類錯誤不屬此類,由任務層 autoretry。
本檔為純 domain,NEVER import 任何框架 / SDK(§C.2)。
"""
from typing import Protocol

from app.domain.entities.document import NormalizedDocument


class ParseError(Exception):
    """文件內容本身無法解析(加密、格式損毀、超出量體上限等)。"""


class DocumentParser(Protocol):
    media_types: tuple[str, ...]  # 註冊鍵(canonical mime)

    def parse(self, data: bytes, filename: str) -> NormalizedDocument: ...
