# 跨平台開發啟動器(Windows PowerShell)。實際邏輯在 scripts/dev.py。
#   .\dev.ps1            啟動全部
#   .\dev.ps1 --setup    一次性安裝依賴
$ErrorActionPreference = 'Stop'
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $py) {
  Write-Error '找不到 python,請先安裝 Python 3.12+'
  exit 1
}
& $py.Source (Join-Path $PSScriptRoot 'scripts\dev.py') @args
