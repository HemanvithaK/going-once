from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.bids import router as bids_router
from app.api.state import router as state_router
from app.core.config import get_settings
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


app = FastAPI(title="going-once-bidding", version="0.1.0", lifespan=lifespan)

# The browser asks before a cross-origin call ("preflight") and only goes ahead if the
# answer names its origin. A bid sends Idempotency-Key, so that header must be allowed
# too, or the browser blocks every bid. Tokens travel in the Authorization header, not
# in cookies, so credentials stay off.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    max_age=600,  # the browser may reuse a preflight answer for ten minutes
)

app.include_router(bids_router)
app.include_router(state_router)


@app.get("/healthz", tags=["ops"])
async def healthz() -> dict[str, str]:
    """Liveness: the process is up. Deliberately touches no dependencies."""
    return {"status": "ok"}


@app.get("/readyz", tags=["ops"])
async def readyz() -> dict[str, str]:
    """Readiness: this instance can actually reach its database."""
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
