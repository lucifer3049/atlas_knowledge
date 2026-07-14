# AI 知識問答平台

自架 AI 知識問答平台:RAG + Tool Calling + 多模型切換,Web 與社群通路(LINE / Discord)共用同一 orchestration 核心。規劃書見 [docs/plan/MASTER_PLAN_v1.md](docs/plan/MASTER_PLAN_v1.md)(全案單一事實來源)。

技術棧:FastAPI + Vite(Vue + TS)+ PostgreSQL 16(pgvector)+ Redis + Celery + Caddy + Docker Compose。

## Repo 結構

```
├── backend/     # FastAPI 應用(P0 T0.2 起)
├── frontend/    # Vite + Vue (P0 T0.4 起)
├── config/      # models.yaml(模型別名登錄)
├── docs/        # 規劃書(docs/plan/)、ADR(docs/adr/)
└── docker-compose.yml
```

## 開發環境啟動

前置:Docker Desktop、Python 3.12+、Node 20+、本地 Ollama(P1 起,聊天用)。

```bash
# 1. 準備環境變數(首次;compose 由根目錄 .env 注入 DB 憑證)
cp .env.example .env

# 2. 啟動基礎設施(PostgreSQL 16 + pgvector 0.8、Redis 7.4)
docker compose up -d

# 3. 確認健康狀態
docker compose ps    # postgres / redis 均應為 healthy
```

開發用連線資訊(與 backend `.env.example` 對齊):

| 服務 | 連線 |
|---|---|
| PostgreSQL | `postgresql://app:app@localhost:5433/app`(host 5433 → 容器 5432,避開本機原生 PostgreSQL) |
| Redis | `redis://localhost:6379/0` |
| Ollama(LLM) | `http://localhost:11434/v1`(`LLM_BASE_URL`) |

## 一鍵開發啟動(dev launcher)

跨平台啟動器,一個終端機把整套本機環境拉起來:起 postgres + redis → 跑 `alembic upgrade head`
→ 同時啟動 backend(uvicorn)、celery worker、frontend(vite)並彙整輸出,`Ctrl+C` 一次全關。
核心邏輯在 [scripts/dev.py](scripts/dev.py)(純標準庫);另附各 OS 外殼 `dev.ps1` / `dev.sh`。

```bash
# 首次:一次性安裝依賴(建 venv + 後端 pip + 前端 npm install)
python scripts/dev.py --setup          # Windows: .\dev.ps1 --setup   /  *nix: ./dev.sh --setup

# 之後每次:啟動全部
python scripts/dev.py                  # Windows: .\dev.ps1           /  *nix: ./dev.sh
#   選項:--no-web(只跑後端/worker)
```

前置需自行就緒(腳本只檢查、不代啟):**Docker Desktop 已啟動**、**Ollama 常駐**且已
`ollama pull`(見 [config/models.yaml](config/models.yaml) 的 `model`,預設 `llama3.1:8b`)。
啟動後開 http://localhost:5173 。

> 這是**開發便利工具**;正式部署(容器化 backend/worker/frontend + Caddy 反代的單一 compose)
> 屬 P7/P8,尚未實作。

## 後端(backend)

前置:已 `docker compose up -d` 啟動 PostgreSQL / Redis。

```bash
cd backend
python -m venv .venv
pip install -e ".[dev]"
cp .env.example .env                   # 依需要調整連線設定
```

**啟用 venv**(後續指令才找得到 `ruff`/`mypy`/`pytest`/`pre-commit`):

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
#   若被執行原則擋下,先跑一次:Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
# macOS/Linux: source .venv/bin/activate
```

啟用後即可用短指令;若不想啟用,把 `pre-commit`/`ruff`/… 換成 `.\.venv\Scripts\<工具>.exe` 亦可。

```bash
# 啟動 API(健康檢查:GET /api/health → {"status":"ok"})
uvicorn app.main:app --reload

# DoD 檢查
ruff check .
mypy .
pytest
# 有新 migration 時:
alembic upgrade head && alembic downgrade -1 && alembic upgrade head
```

> **測試需要 PostgreSQL**:API / repository 層測試會依 `DATABASE_URL` 自動建立獨立測試庫
> `<db>_test`(例:`app_test`)、跑 `alembic upgrade` 建 schema,每則測試在交易內執行後 rollback。
> 因此跑 `pytest` 前需先 `docker compose up -d postgres`。`backend/.env` 另需 `JWT_SECRET`
> (見 `.env.example`;正式環境務必換成 64 bytes 隨機值,只從環境注入)。

## 前端(frontend)

技術棧:Vite 5 + Vue 3(`<script setup>`)+ TypeScript strict + Tailwind CSS v4 + ESLint 9(flat config)+ Prettier + Vitest。需 Node 20+。

```bash
cd frontend
npm install

npm run dev         # 開發伺服器(預設 http://localhost:5173)
npm run build       # vue-tsc 型別檢查 + production build

# DoD 檢查
npm run lint        # ESLint
npm run typecheck   # vue-tsc --noEmit
npm run test        # Vitest
```

> 目前為腳手架階段,僅一個占位頁面與煙霧測試;實際頁面(登入、聊天等)自 P1 起逐步加入。

## 工具鏈與 CI

- **pre-commit**([.pre-commit-config.yaml](.pre-commit-config.yaml)):提交前跑基本檔案檢查(trailing whitespace / EOF / YAML / TOML / merge conflict)+ backend `ruff --fix`。安裝(於 repo 根目錄執行一次):

  ```bash
  python -m pip install pre-commit   # 建議裝到全域 Python(GUI/CLI 提交皆可用)
  pre-commit install
  ```

  > 注意:本 repo 路徑含中文。**勿用 venv 內的 pre-commit 安裝掛勾**——它會把含中文的絕對路徑寫進 `.git/hooks/pre-commit` 而損毀,導致提交時報 `pre-commit not found`。用路徑全為 ASCII 的全域 Python 安裝即可避免。
- **GitHub Actions**([.github/workflows/ci.yml](.github/workflows/ci.yml)):每次 push(main)/PR 跑兩個 job——backend(`ruff check` → `mypy` → `pytest`,Python 3.12,附 `pgvector/pgvector` PostgreSQL service 供 DB 測試)與 frontend(`lint` → `typecheck` → `test`,Node 20)。CI 不呼叫任何外部 LLM / 平台 API。
