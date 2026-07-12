import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.anyio
async def test_health_response_carries_generated_request_id() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")

    assert resp.status_code == 200
    request_id = resp.headers.get("X-Request-ID")
    assert request_id  # middleware 產生並回寫


@pytest.mark.anyio
async def test_client_supplied_request_id_is_echoed() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health", headers={"X-Request-ID": "trace-abc-123"})

    assert resp.headers.get("X-Request-ID") == "trace-abc-123"
