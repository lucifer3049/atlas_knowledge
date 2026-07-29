"""OpenAI-compatible embeddings adapter(T2.4;PHASE_2 §5、D13)。

實作 `domain.ports.embedding.EmbeddingProvider`。POST `/embeddings`,一個 adapter 打多家
(Ollama bge-m3 / OpenAI / 本地推理伺服器)。供應商差異於此吸收,httpx 例外 NEVER 洩漏——
一律映射為 `EmbeddingError`(§6 五類),維度不符歸 `provider_error`。

錯誤映射與 LLM adapter 同表(§7):401/403→auth、429→rate_limited、
400 含 context/length→context_length、5xx/timeout/連線→transient、其餘→provider_error。
"""
from collections.abc import Sequence

import httpx
import structlog

from app.domain.ports.embedding import EmbeddingError
from app.domain.ports.llm import ProviderErrorCode

_logger = structlog.get_logger()

_ERROR_MESSAGES: dict[ProviderErrorCode, str] = {
    "auth": "embedding 服務驗證失敗",
    "rate_limited": "embedding 服務忙碌中,請稍後再試",
    "context_length": "待嵌入文字過長,超出模型上限",
    "transient": "embedding 服務暫時無法回應,請稍後再試",
    "provider_error": "embedding 服務回應異常",
}


class OpenAICompatEmbedding:
    name = "openai_compat"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        version: str,
        dim: int,
        timeout_s: float,
        batch_size: int,
    ) -> None:
        self.version = version
        self.dim = dim
        self._model = model
        self._batch_size = max(1, batch_size)
        self._url = f"{base_url.rstrip('/')}/embeddings"
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(connect=10, read=timeout_s, write=10, pool=10),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            vectors.extend(await self._embed_batch(batch))
        return vectors

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        try:
            resp = await self._client.post(
                self._url, json={"model": self._model, "input": batch}
            )
        except httpx.TimeoutException as exc:
            raise self._error("transient") from exc
        except httpx.HTTPError as exc:
            raise self._error("transient") from exc

        if resp.status_code >= 400:
            raise self._error(self._map_http_status(resp.status_code, resp.content))

        try:
            data = resp.json()["data"]
            # OpenAI 保證回傳含 index,但不保證排序;依 index 還原輸入順序。
            ordered = sorted(data, key=lambda row: row["index"])
            vectors = [[float(x) for x in row["embedding"]] for row in ordered]
        except (KeyError, TypeError, ValueError) as exc:
            raise self._error("provider_error") from exc

        if len(vectors) != len(batch):
            raise self._error("provider_error")
        for vector in vectors:
            if len(vector) != self.dim:
                # 維度綁定 DB 欄位;不符即無法落庫,是不可容忍的契約違反。
                raise self._error(
                    "provider_error",
                    detail=f"embedding 維度 {len(vector)} != 期望 {self.dim}",
                )
        return vectors

    @staticmethod
    def _map_http_status(status: int, raw: bytes) -> ProviderErrorCode:
        if status in (401, 403):
            return "auth"
        if status == 429:
            return "rate_limited"
        if status == 400:
            text = raw.decode("utf-8", "ignore").lower()
            if "context" in text or "length" in text:
                return "context_length"
            return "provider_error"
        if status >= 500:
            return "transient"
        return "provider_error"

    def _error(self, code: ProviderErrorCode, *, detail: str | None = None) -> EmbeddingError:
        if detail is not None:
            _logger.warning("embedding.error", code=code, detail=detail)
        return EmbeddingError(code, _ERROR_MESSAGES[code])
