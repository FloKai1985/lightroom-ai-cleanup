"""FastAPI app factory.

Binds to `127.0.0.1` only by default (see `run()` below and
docs/safety.md) — this service must never be reachable from the network
without a deliberate, separately-reviewed change.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from lr_cleanup import __version__
from lr_cleanup.api import jobs, results
from lr_cleanup.config import Settings, get_settings
from lr_cleanup.database.session import init_db, make_engine, make_session_factory, session_scope
from lr_cleanup.logging_config import configure_logging

logger = structlog.get_logger(__name__)

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a fully wired app. Pass `settings` explicitly for tests that
    need an isolated database instead of the process-wide `.env` config."""
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(resolved_settings.database_url)
        init_db(engine)
        app.state.settings = resolved_settings
        app.state.session_factory = make_session_factory(engine)
        logger.info("service.started", database_url=resolved_settings.database_url)
        yield
        engine.dispose()
        logger.info("service.stopped")

    app = FastAPI(
        title="Lightroom AI Cleanup",
        version=__version__,
        lifespan=lifespan,
    )
    app.include_router(jobs.router)
    app.include_router(results.router)

    @app.get("/health")
    def health(request: Request) -> JSONResponse:
        try:
            with session_scope(request.app.state.session_factory) as session:
                session.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001 - health check must never 500
            logger.warning("health.database_unreachable", error=str(exc))
            return JSONResponse(
                {"status": "degraded", "version": __version__, "database": "unreachable"},
                status_code=503,
            )
        return JSONResponse({"status": "ok", "version": __version__, "database": "ok"})

    return app


app = create_app()


def run() -> None:
    """Entry point for the `lr-cleanup-server` console script."""
    import uvicorn

    settings = get_settings()
    if settings.host not in _LOOPBACK_HOSTS:
        raise RuntimeError(
            f"Refusing to bind to non-loopback host {settings.host!r}. "
            "This service must stay local-only by default — see docs/safety.md."
        )
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    run()
