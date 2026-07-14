"""在 sync Celery 任務內執行 async 邏輯的唯一入口(§F.3:單一 run_async() helper)。

每次呼叫建立獨立 event loop(asyncio.run):配合 prefork worker,且 asyncpg 連線
綁定當前 loop,故 engine/session 一律於 coroutine 內建立、用完即棄。
"""
import asyncio
from collections.abc import Coroutine


def run_async[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)
