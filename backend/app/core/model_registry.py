"""`config/models.yaml` 讀取(schema 權威:phase-6 §7.1;P1 起即用此格式,§R R2)。

T1.2 僅需:預設 alias 解析、alias 是否存在(建立 conversation 時驗證)。
adapter 組裝(base_url/api_key/params)於 T1.3 再擴充。模組層載入一次並快取。
"""
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

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
