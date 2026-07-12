"""Keyset 游標分頁工具(§10.2)。

cursor = base64url(`"{iso_ts}|{uuid}"`);排序一律 (ts desc, id desc)。
以欄位型別比較(展開 OR 形式)避免 row-value 比較的型別推斷問題。
"""
import base64
from collections.abc import Callable, Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, and_, or_
from sqlalchemy.orm import InstrumentedAttribute

from app.core.errors import InvalidCursor


def encode_cursor(ts: datetime, id_: UUID) -> str:
    raw = f"{ts.isoformat()}|{id_}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        ts_str, id_str = raw.split("|", 1)
        return datetime.fromisoformat(ts_str), UUID(id_str)
    except ValueError as exc:  # base64 / utf-8 / isoformat / uuid / unpack 皆為 ValueError 系
        raise InvalidCursor() from exc


def keyset_before(
    ts_col: InstrumentedAttribute[datetime],
    id_col: InstrumentedAttribute[UUID],
    ts: datetime,
    id_: UUID,
) -> ColumnElement[bool]:
    """(ts_col, id_col) < (ts, id_) 的展開形式。"""
    return or_(ts_col < ts, and_(ts_col == ts, id_col < id_))


def paginate[T](
    rows: Sequence[T],
    limit: int,
    key: Callable[[T], tuple[datetime, UUID]],
) -> tuple[list[T], str | None]:
    """rows 已多取一筆(limit+1)。回傳 (本頁 items, next_cursor)。"""
    has_more = len(rows) > limit
    items = list(rows[:limit])
    next_cursor = encode_cursor(*key(items[-1])) if has_more and items else None
    return items, next_cursor
