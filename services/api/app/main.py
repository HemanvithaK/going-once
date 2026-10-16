from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from sqlalchemy import text

from app.api.auth import router as auth_router
from app.api.listings import router as listings_router
from app.core.db import engine
from app.core.logging import configure_logging, get_logger

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Runs once on startup, and again after the last request on shutdown."""
    configure_logging()
    log.info("startup")
    yield
    await engine.dispose()
    log.info("shutdown")


app = FastAPI(title="going-once-api", version="0.1.0", lifespan=lifespan)

app.include_router(auth_router)
app.include_router(listings_router)

@app.get("/healthz", tags=["ops"])
async def healthz() -> dict[str, str]:
    """Liveness: the process is up. Deliberately touches no dependencies."""
    return {"status": "ok"}


@app.get("/readyz", tags=["ops"])
async def readyz() -> dict[str, str]:
    """Readiness: this instance can actually serve traffic."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        log.warning("readiness_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        ) from exc
    return {"status": "ready"}