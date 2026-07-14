./#!/usr/bin/env bash
# 跨平台開發啟動器(macOS / Linux)。實際邏輯在 scripts/dev.py。
#   ./dev.sh            啟動全部
#   ./dev.sh --setup    一次性安裝依賴
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "找不到 python3,請先安裝 Python 3.12+" >&2
  exit 1
fi
exec "$PY" "$DIR/scripts/dev.py" "$@"
