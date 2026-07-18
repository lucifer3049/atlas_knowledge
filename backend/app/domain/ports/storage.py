"""`ObjectStorage` port(凍結契約;PHASE_2 §5)。

物件儲存的唯一抽象:v1 為本機檔案系統,觸發條件成立後換 MinIO/S3 只動 adapter
(§C.7.4)。key 佈局:`documents/{document_id}/original{ext}`、
`documents/{document_id}/normalized.json`;purge 以 `documents/{document_id}/` prefix 刪除。
DB 只存 key,NEVER 存絕對路徑。本檔為純 domain,NEVER import 任何框架 / SDK。
"""
from typing import Protocol


class ObjectStorage(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def delete_prefix(self, prefix: str) -> None: ...
