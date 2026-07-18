"""`config/models.yaml` 讀取(schema 權威:phase-6 §7.1;P1 起即用此格式,§R R2)。

- 預設 alias 解析、alias 是否存在(建立 conversation 時驗證,T1.2)。
- `resolve(alias)`:解析 alias 的實際 model 名與參數(T1.4;orchestrator 以
  `conversation.model_alias` 查此表組 `ModelParams`)。連線層(base_url/api_key/timeout)
  於 P1 由 settings 提供(yaml `base_url: ${LLM_BASE_URL}` 即等於 `settings.llm_base_url`);
  多 provider / per-alias base_url 的 ModelRouter 為 P6。
模組層載入一次並快取。
"""
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import BaseModel

_logger = structlog.get_logger()

# backend/app/core/model_registry.py → parents: [0]=core [1]=app [2]=backend [3]=repo 根
_MODELS_YAML = Path(__file__).resolve().parents[3] / "config" / "models.yaml"


@lru_cache(maxsize=1)
def _config() -> dict[str, Any]:
    with _MODELS_YAML.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise RuntimeError(f"models.yaml 格式錯誤(非 mapping):{_MODELS_YAML}")
    return data


def default_alias() -> str:
    return str(_config()["default_alias"])


def alias_exists(alias: str) -> bool:
    aliases = _config().get("aliases", {})
    return isinstance(aliases, dict) and alias in aliases


class ResolvedModel(BaseModel):
    provider: str
    model: str
    temperature: float = 0.7
    max_tokens: int | None = None


def resolve(alias: str) -> ResolvedModel:
    """alias → 實際 model + 參數。

    alias 不存在(建立 conversation 後才自 yaml 移除)→ fallback 到 default_alias
    並 log warning,舊對話 NEVER 因組態演進而 500(與 phase-6 §7.2 解析優先序
    「conversation → default_alias」同語意;完整 ModelRouter 為 P6)。
    default_alias 本身缺失為組態錯誤 → KeyError fail-fast。
    """
    aliases = _config().get("aliases", {})
    if alias not in aliases:
        _logger.warning("model_alias_fallback", alias=alias, fallback=default_alias())
        alias = default_alias()
    cfg = aliases[alias]
    params = cfg.get("params", {}) or {}
    return ResolvedModel(
        provider=cfg["provider"],
        model=cfg["model"],
        temperature=params.get("temperature", 0.7),
        max_tokens=params.get("max_tokens"),
    )
