"""測試設定與 DB fixtures(PHASE_1 §12.1)。

策略:獨立測試資料庫(`<db>_test`),session 範圍內以 alembic upgrade 建 schema;
每個測試在單一連線的外層交易內跑,結束 rollback 達成隔離(app 的 commit 以 savepoint
join,rollback 一併還原)。CI / 本地皆由 settings.database_url 推導測試庫 URL。
"""
import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.main import app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_BASE_URL = make_url(settings.database_url)
_TEST_DB = f"{_BASE_URL.database}_test"
_TEST_URL = _BASE_URL.set(database=_TEST_DB)
_TEST_URL_STR = _TEST_URL.render_as_string(hide_password=False)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _recreate_test_database() -> None:
    admin = create_async_engine(
        _BASE_URL.render_as_string(hide_password=False), isolation_level="AUTOCOMMIT"
    )
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{_TEST_DB}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{_TEST_DB}"'))
    finally:
        await admin.dispose()


@pytest.fixture(scope="session")
def _migrated_test_db() -> Iterator[str]:
    asyncio.run(_recreate_test_database())
    env = {**os.environ, "DATABASE_URL": _TEST_URL_STR}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_BACKEND_DIR,
        env=env,
        check=True,
        capture_output=True,
    )
    yield _TEST_URL_STR


@pytest.fixture
async def db_connection(_migrated_test_db: str) -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(_migrated_test_db)
    conn = await engine.connect()
    trans = await conn.begin()
    try:
        yield conn
    finally:
        await trans.rollback()
        await conn.close()
        await engine.dispose()


@pytest.fixture
def session_factory(
    db_connection: AsyncConnection,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=db_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    app.state.session_factory = session_factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
