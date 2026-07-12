import pytest
import structlog
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.api.middleware import RequestContextMiddleware
from app.core.errors import AppError, register_error_handlers


class _SampleNotFound(AppError):
    code = "conversation_not_found"
    http_status = 404
    message = "找不到對話"


class _Body(BaseModel):
    name: str


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)

    @app.get("/app-error")
    async def _raise_app_error() -> None:
        raise _SampleNotFound()

    @app.get("/boom")
    async def _raise_unhandled() -> None:
        raise RuntimeError("super secret internal detail")

    @app.post("/validate")
    async def _validate(body: _Body) -> dict[str, str]:
        return {"name": body.name}

    return app


@pytest.mark.anyio
async def test_app_error_renders_frozen_envelope() -> None:
    transport = ASGITransport(app=_build_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/app-error")

    assert resp.status_code == 404
    error = resp.json()["error"]
    assert set(error.keys()) == {"code", "message", "trace_id"}
    assert error["code"] == "conversation_not_found"
    assert error["message"] == "找不到對話"
    assert error["trace_id"]
    assert resp.headers.get("X-Request-ID") == error["trace_id"]


@pytest.mark.anyio
async def test_request_validation_error_maps_to_validation_error() -> None:
    transport = ASGITransport(app=_build_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/validate", json={})

    assert resp.status_code == 422
    error = resp.json()["error"]
    assert set(error.keys()) == {"code", "message", "trace_id"}
    assert error["code"] == "validation_error"
    assert error["trace_id"]


@pytest.mark.anyio
async def test_unhandled_exception_is_masked_and_logged_with_traceback() -> None:
    transport = ASGITransport(app=_build_app(), raise_app_exceptions=False)
    with structlog.testing.capture_logs() as captured:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/boom")

    assert resp.status_code == 500
    error = resp.json()["error"]
    assert set(error.keys()) == {"code", "message", "trace_id"}
    assert error["code"] == "internal_error"
    # 內部細節 NEVER 外洩到回應
    assert "secret" not in error["message"]
    assert error["trace_id"]
    assert resp.headers.get("X-Request-ID") == error["trace_id"]
    # traceback 只進 log
    assert any(
        event.get("event") == "unhandled_exception" and event.get("exc_info")
        for event in captured
    )
