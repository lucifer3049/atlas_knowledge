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
# 1. 啟動基礎設施(PostgreSQL 16 + pgvector 0.8、Redis 7.4)
docker compose up -d

# 2. 確認健康狀態
docker compose ps    # postgres / redis 均應為 healthy
```

開發用連線資訊(與 backend `.env.example` 對齊):

| 服務 | 連線 |
|---|---|
| PostgreSQL | `postgresql://app:app@localhost:5433/app`(host 5433 → 容器 5432,避開本機原生 PostgreSQL) |
| Redis | `redis://localhost:6379/0` |
| Ollama(LLM) | `http://localhost:11434/v1`(`LLM_BASE_URL`) |

## 後端(backend)

前置:已 `docker compose up -d` 啟動 PostgreSQL / Redis。

```bash
cd backend
python -m venv .venv
# Windows: .\.venv\Scripts\activate    # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env                   # 依需要調整連線設定

# 啟動 API(健康檢查:GET /api/health → {"status":"ok"})
uvicorn app.main:app --reload

# DoD 檢查
ruff check .
mypy .
pytest
# 有新 migration 時:
alembic upgrade head && alembic downgrade -1 && alembic upgrade head
```

前端的安裝/測試指令將於 T0.4(frontend 腳手架)補充。
