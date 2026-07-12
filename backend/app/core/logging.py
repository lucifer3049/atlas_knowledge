"""structlog 設定(全案唯一入口)。

JSON 輸出、所有 log 帶 request_id(由 middleware 以 contextvars 注入)。
processor 層設 redact 清單兜底:密碼 / token / cookie 等敏感 key 一律遮蔽
(MASTER_PLAN_v1 §C.5.6)。
"""
import logging

import structlog
from structlog.typing import EventDict, WrappedLogger

# key 命中即遮蔽(小寫比對);兜底防護,不取代呼叫端本身不 log 敏感值的紀律。
_REDACT_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "token",
        "access_token",
        "refresh_token",
        "rt",
        "authorization",
        "cookie",
        "set-cookie",
        "jwt_secret",
    }
)


def _redact_sensitive(logger: WrappedLogger, name: str, event_dict: EventDict) -> EventDict:
    for key in list(event_dict):
        if key.lower() in _REDACT_KEYS:
            event_dict[key] = "<redacted>"
    return event_dict


def configure_logging(level: int = logging.INFO) -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact_sensitive,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
