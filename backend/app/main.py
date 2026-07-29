from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.middleware import RequestContextMiddleware
from app.api.routers import auth, chat, conversations, documents, health, me
from app.core.config import settings
from app.core.db import create_engine
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.redis import create_redis_client
from app.core.wiring import build_embedding, build_llm, build_retrieval
from app.infrastructure.db.session import create_session_factory

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine = create_engine()
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.llm = build_llm(settings)  # 單一 adapter 掛 app.state(§R R2:模組層載一次)
    app.state.embedding = build_embedding(settings)
    app.state.redis = create_redis_client(settings)
    app.state.retrieval = build_retrieval(
        settings,
        session_factory=app.state.session_factory,
        embedding=app.state.embedding,
        redis=app.state.redis,
    )
    try:
        yield
    finally:
        for adapter in (app.state.llm, app.state.embedding):
            aclose = getattr(adapter, "aclose", None)
            if aclose is not None:
                await aclose()
        await app.state.redis.aclose()
        await engine.dispose()


app = FastAPI(title="AI 知識問答平台 API", lifespan=lifespan)
app.add_middleware(RequestContextMiddleware)
register_error_handlers(app)
app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(me.router, prefix="/api")
app.include_router(conversations.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
