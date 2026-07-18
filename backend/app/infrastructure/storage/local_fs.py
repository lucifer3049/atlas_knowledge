"""LocalFileStorage:`ObjectStorage` port 的本機檔案系統 adapter(T2.1;PHASE_2 §5)。

阻塞 I/O 一律以 `asyncio.to_thread` 移出事件迴圈(§FastAPI 慣例:async 路徑 NEVER 阻塞)。
key 一律視為相對路徑;逸出 root 的 key(絕對路徑或 `..`)直接 ValueError,
NEVER 讓外部輸入決定寫入位置。
"""
import asyncio
import shutil
from pathlib import Path


class LocalFileStorage:
    def __init__(self, root: str) -> None:
        self._root = Path(root).resolve()

    def _resolve(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if path != self._root and self._root not in path.parents:
            raise ValueError("storage key 逸出 root")
        return path

    async def put(self, key: str, data: bytes) -> None:
        path = self._resolve(key)
        await asyncio.to_thread(self._write, path, data)

    async def get(self, key: str) -> bytes:
        path = self._resolve(key)
        return await asyncio.to_thread(path.read_bytes)

    async def delete_prefix(self, prefix: str) -> None:
        path = self._resolve(prefix)
        # 不存在即無事可做(purge 天然冪等,§8.2)。
        await asyncio.to_thread(shutil.rmtree, path, True)

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
