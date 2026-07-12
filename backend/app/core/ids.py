"""全案唯一 ID 產生入口:主鍵一律 UUIDv7(MASTER_PLAN_v1 §C.5.2)。"""
from uuid import UUID

from uuid_utils.compat import uuid7


def new_id() -> UUID:
    return uuid7()
