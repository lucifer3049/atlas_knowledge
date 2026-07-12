"""應用層錯誤階層與全域 exception handler。

所有非 2xx 回應一律走凍結錯誤格式(MASTER_PLAN_v1 §C.5.1、PHASE_1 §10.1):

    {"error": {"code": "<machine_code>", "message": "<人類可讀繁中>", "trace_id": "<id>"}}

錯誤碼登錄表為凍結契約(PHASE_1 §10.4);新增碼需標 OPEN QUESTION。
具體 AppError 子類(ConversationNotFound、InvalidCredentials …)由各自 ticket 引入,
本模組僅提供基底與 handler 骨架(T1.5)。
"""
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

_logger = structlog.get_logger()


class AppError(Exception):
    """應用層已知錯誤基底。子類以 class 屬性宣告凍結的 code / http_status / message。"""

    code: str
    http_status: int
    message: str

    def __init__(self, message: str | None = None) -> None:
        if message is not None:
            self.message = message
        super().__init__(getattr(self, "message", type(self).__name__))


class EmailAlreadyRegistered(AppError):
    code = "email_already_registered"
    http_status = 409
    message = "此 email 已被註冊"


class InvalidCredentials(AppError):
    # 帳號不存在與密碼錯誤一律回應完全相同,NEVER 洩漏帳號是否存在(§5.3-2)。
    code = "invalid_credentials"
    http_status = 401
    message = "帳號或密碼錯誤"


class UserInactive(AppError):
    code = "user_inactive"
    http_status = 403
    message = "帳號已停用"


class InvalidToken(AppError):
    code = "invalid_token"
    http_status = 401
    message = "憑證無效或已過期"


class InvalidRefreshToken(AppError):
    code = "invalid_refresh_token"
    http_status = 401
    message = "登入憑證已失效,請重新登入"


def _trace_id(request: Request) -> str:
    # middleware 一定會設定 request_id;fallback 僅防禦性。
    request_id: str | None = getattr(request.state, "request_id", None)
    return request_id or uuid4().hex


def _envelope(code: str, message: str, trace_id: str, status: int) -> JSONResponse:
    # trace_id 同時寫回 X-Request-ID:涵蓋 ServerErrorMiddleware(最外層,不經 middleware
    # send wrapper)產生的 500 回應。
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "trace_id": trace_id}},
        headers={"X-Request-ID": trace_id},
    )


async def _handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return _envelope(exc.code, exc.message, _trace_id(request), exc.http_status)


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    return _envelope("validation_error", "請求參數驗證失敗", _trace_id(request), 422)


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    trace_id = _trace_id(request)
    # 細節(含 traceback)只進 log;對外訊息固定,NEVER 洩漏內部細節。
    _logger.error("unhandled_exception", exc_info=exc, error_type=type(exc).__name__)
    return _envelope("internal_error", "伺服器發生非預期錯誤,請稍後再試", trace_id, 500)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
