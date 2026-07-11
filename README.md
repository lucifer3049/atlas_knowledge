# AI 知識問答平台

自架 AI 知識問答平台:RAG + Tool Calling + 多模型切換,Web 與社群通路(LINE / Discord)共用同一 orchestration 核心。規劃書見 [docs/plan/MASTER_PLAN_v1.md](docs/plan/MASTER_PLAN_v1.md)(全案單一事實來源)。

技術棧:FastAPI + Vite(React + TS)+ PostgreSQL 16(pgvector)+ Redis + Celery + Caddy + Docker Compose。

## Repo 結構

```
├── backend/     # FastAPI 應用(P0 T0.2 起)
├── frontend/    # Vite + React SPA(P0 T0.4 起)
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
| PostgreSQL | `postgresql://app:app@localhost:5432/app` |
| Redis | `redis://localhost:6379/0` |
| Ollama(LLM) | `http://localhost:11434/v1`(`LLM_BASE_URL`) |

後端與前端的安裝/測試指令將於 T0.2(backend 腳手架)、T0.4(frontend 腳手架)補充。
