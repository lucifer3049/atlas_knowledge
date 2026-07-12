"""session factory 建立(唯一入口)。engine 由 `core/db.py` 提供;
lifespan 於 `app.state` 掛 engine + session_factory(PHASE_1 §11)。
"""
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
