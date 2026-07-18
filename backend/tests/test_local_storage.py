"""LocalFileStorage(ObjectStorage port 的 local FS adapter;T2.1、PHASE_2 §5)單元測試。"""
from pathlib import Path

import pytest

from app.infrastructure.storage.local_fs import LocalFileStorage

pytestmark = pytest.mark.anyio


async def test_put_then_get_roundtrip(tmp_path: Path) -> None:
    storage = LocalFileStorage(str(tmp_path))
    await storage.put("documents/abc/original.txt", b"payload")
    assert await storage.get("documents/abc/original.txt") == b"payload"


async def test_put_creates_parent_directories(tmp_path: Path) -> None:
    storage = LocalFileStorage(str(tmp_path))
    await storage.put("documents/abc/original.pdf", b"x")
    assert (tmp_path / "documents" / "abc" / "original.pdf").is_file()


async def test_put_overwrites_existing_key(tmp_path: Path) -> None:
    storage = LocalFileStorage(str(tmp_path))
    await storage.put("k/f.txt", b"old")
    await storage.put("k/f.txt", b"new")
    assert await storage.get("k/f.txt") == b"new"


async def test_get_missing_key_raises(tmp_path: Path) -> None:
    storage = LocalFileStorage(str(tmp_path))
    with pytest.raises(FileNotFoundError):
        await storage.get("nope.txt")


async def test_delete_prefix_removes_subtree_and_is_idempotent(tmp_path: Path) -> None:
    storage = LocalFileStorage(str(tmp_path))
    await storage.put("documents/abc/original.txt", b"a")
    await storage.put("documents/abc/normalized.json", b"b")
    await storage.put("documents/other/original.txt", b"c")

    await storage.delete_prefix("documents/abc/")
    assert not (tmp_path / "documents" / "abc").exists()
    assert (tmp_path / "documents" / "other" / "original.txt").is_file()

    await storage.delete_prefix("documents/abc/")  # 重跑無副作用


@pytest.mark.parametrize("key", ["../escape.txt", "documents/../../escape.txt", "/abs.txt"])
async def test_keys_escaping_root_are_rejected(tmp_path: Path, key: str) -> None:
    storage = LocalFileStorage(str(tmp_path))
    with pytest.raises(ValueError):
        await storage.put(key, b"x")
