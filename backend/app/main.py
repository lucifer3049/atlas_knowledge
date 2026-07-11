from fastapi import FastAPI

from app.api.routers import health

app = FastAPI(title="AI 知識問答平台 API")
app.include_router(health.router, prefix="/api")
