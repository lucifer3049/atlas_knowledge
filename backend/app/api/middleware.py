"""Request context middleware(pure ASGI)。

- 讀 `X-Request-ID`(自帶則沿用),否則以 uuid7 hex 生成。
- 寫入 `scope["state"]["request_id"]`(= `trace_id`,P1 兩者同值,MASTER_PLAN_v1 §C.5.2)。
- 以 structlog contextvars 注入,使同一請求內所有 log 帶 request_id。
- 於回應 `http.response.start` 補回 `X-Request-ID` header(已存在則不重複)。

採 pure ASGI(非 BaseHTTPMiddleware):contextvars 綁定與下游 endpoint 在同一
執行脈絡,確保 log 能取到 request_id。
"""
import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.ids import new_id

_HEADER = b"x-request-id"


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(_HEADER)
        request_id = incoming.decode("latin-1") if incoming else new_id().hex

        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        state["trace_id"] = request_id

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                if not any(key.lower() == _HEADER for key, _ in headers):
                    headers.append((_HEADER, request_id.encode("latin-1")))
            await send(message)

        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            await self.app(scope, receive, send_with_header)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
