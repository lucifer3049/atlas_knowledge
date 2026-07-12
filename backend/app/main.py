from fastapi import FastAPI

from app.api.middleware import RequestContextMiddleware
from app.api.routers import health
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging

configure_logging()

app = FastAPI(title="AI 知識問答平台 API")
app.add_middleware(RequestContextMiddleware)
register_error_handlers(app)
app.include_router(health.router, prefix="/api")
