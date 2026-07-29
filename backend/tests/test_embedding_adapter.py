"""OpenAI-compatible embeddings adapter 測試(T2.4;PHASE_2 §12.1)。

respx 錄放攔截 httpx,CI NEVER 打真實 API(§0.3-10)。
涵蓋:正常(順序還原)、批次、空輸入、維度不符 → provider_error、HTTP/連線錯誤映射。
"""
import httpx
import pytest
import respx

from app.domain.ports.embedding import EmbeddingError
from app.infrastructure.embedding.openai_compat import OpenAICompatEmbedding

pytestmark = pytest.mark.anyio

_BASE_URL = "http://emb.test/v1"
_URL = f"{_BASE_URL}/embeddings"


def _provider(*, dim: int = 4, batch_size: int = 32) -> OpenAICompatEmbedding:
    return OpenAICompatEmbedding(
        base_url=_BASE_URL,
        api_key="secret",
        model="bge-m3",
        version="bge-m3@4",
        dim=dim,
        timeout_s=5,
        batch_size=batch_size,
    )


def _data(*vectors: list[float]) -> dict[str, object]:
    return {"data": [{"embedding": v, "index": i} for i, v in enumerate(vectors)]}


async def _run(provider: OpenAICompatEmbedding, texts: list[str]) -> list[list[float]]:
    try:
        return await provider.embed(texts)
    finally:
        await provider.aclose()


@respx.mock
async def test_embed_returns_vectors_in_input_order() -> None:
    respx.post(_URL).mock(
        return_value=httpx.Response(200, json=_data([1.0, 0, 0, 0], [0, 1.0, 0, 0]))
    )
    vectors = await _run(_provider(), ["甲", "乙"])
    assert vectors == [[1.0, 0, 0, 0], [0, 1.0, 0, 0]]


@respx.mock
async def test_embed_restores_order_by_index() -> None:
    # provider 不保證排序:回傳 index 亂序,adapter 必須依 index 還原
    out_of_order = {"data": [
        {"embedding": [0, 1.0, 0, 0], "index": 1},
        {"embedding": [1.0, 0, 0, 0], "index": 0},
    ]}
    respx.post(_URL).mock(return_value=httpx.Response(200, json=out_of_order))
    vectors = await _run(_provider(), ["甲", "乙"])
    assert vectors == [[1.0, 0, 0, 0], [0, 1.0, 0, 0]]


@respx.mock
async def test_embed_batches_by_batch_size() -> None:
    route = respx.post(_URL).mock(
        side_effect=[
            httpx.Response(200, json=_data([1.0, 0, 0, 0], [0, 1.0, 0, 0])),
            httpx.Response(200, json=_data([0, 0, 1.0, 0])),
        ]
    )
    vectors = await _run(_provider(batch_size=2), ["a", "b", "c"])
    assert route.call_count == 2
    assert vectors == [[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0]]
    # 第一批送 2 筆、第二批送 1 筆
    import json

    assert json.loads(route.calls[0].request.content)["input"] == ["a", "b"]
    assert json.loads(route.calls[1].request.content)["input"] == ["c"]


@respx.mock
async def test_empty_input_never_calls_api() -> None:
    route = respx.post(_URL)
    assert await _run(_provider(), []) == []
    assert route.call_count == 0


@respx.mock
async def test_dimension_mismatch_maps_to_provider_error() -> None:
    respx.post(_URL).mock(return_value=httpx.Response(200, json=_data([1.0, 0, 0])))  # dim 3 != 4
    with pytest.raises(EmbeddingError) as exc:
        await _run(_provider(dim=4), ["甲"])
    assert exc.value.code == "provider_error"


@respx.mock
async def test_wrong_count_maps_to_provider_error() -> None:
    respx.post(_URL).mock(return_value=httpx.Response(200, json=_data([1.0, 0, 0, 0])))
    with pytest.raises(EmbeddingError) as exc:
        await _run(_provider(), ["甲", "乙"])  # 送 2 回 1
    assert exc.value.code == "provider_error"


@respx.mock
async def test_malformed_body_maps_to_provider_error() -> None:
    respx.post(_URL).mock(return_value=httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(EmbeddingError) as exc:
        await _run(_provider(), ["甲"])
    assert exc.value.code == "provider_error"


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (401, b'{"e":"x"}', "auth"),
        (403, b'{"e":"x"}', "auth"),
        (429, b'{"e":"x"}', "rate_limited"),
        (400, b'{"error":"maximum context length"}', "context_length"),
        (400, b'{"error":"bad"}', "provider_error"),
        (500, b"boom", "transient"),
    ],
)
@respx.mock
async def test_http_status_maps_to_error_code(status: int, body: bytes, code: str) -> None:
    respx.post(_URL).mock(return_value=httpx.Response(status, content=body))
    with pytest.raises(EmbeddingError) as exc:
        await _run(_provider(), ["甲"])
    assert exc.value.code == code


@respx.mock
async def test_timeout_maps_to_transient() -> None:
    respx.post(_URL).mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(EmbeddingError) as exc:
        await _run(_provider(), ["甲"])
    assert exc.value.code == "transient"


@respx.mock
async def test_connect_error_maps_to_transient() -> None:
    respx.post(_URL).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(EmbeddingError) as exc:
        await _run(_provider(), ["甲"])
    assert exc.value.code == "transient"


@respx.mock
async def test_api_key_in_header_never_in_body() -> None:
    route = respx.post(_URL).mock(return_value=httpx.Response(200, json=_data([1.0, 0, 0, 0])))
    await _run(_provider(), ["甲"])
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer secret"
    assert b"secret" not in request.content
