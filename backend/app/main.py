from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.middleware import RequestContextMiddleware
from app.api.routers import auth, health, me
from app.core.db import create_engine
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.infrastructure.db.session import create_session_factory

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine = create_engine()
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    try:
        yield
    finally:
        await engine.dispose()


app = FastAPI(title="AI 知識問答平台 API", lifespan=lifespan)
app.add_middleware(RequestContextMiddleware)
register_error_handlers(app)
app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(me.router, prefix="/api")
