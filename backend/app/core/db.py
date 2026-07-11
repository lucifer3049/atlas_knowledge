from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import settings


def create_engine() -> AsyncEngine:
    """建立 async engine(唯一 engine 工廠;Alembic 與應用共用)。"""
    return create_async_engine(settings.database_url, future=True)
