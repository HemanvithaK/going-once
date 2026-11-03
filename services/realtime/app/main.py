from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status

from app.api.watch import router as watch_router
from app.core.bidding_client import close_bidding_client
from app.core.hub import hub
from app.core.logging import configure_logging, get_logger
from app.core.redis import close_redis, redis_ping

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Runs once on startup, and again after the last request on shutdown."""
    configure_logging()
    log.info("startup")
    yield
    await hub.close()
    await close_bidding_client()
    await close_redis()
    log.info("shutdown")


app = FastAPI(title="going-once-realtime", version="0.1.0", lifespan=lifespan)
app.include_router(watch_router)


@app.get("/healthz", tags=["ops"])
async def healthz() -> dict[str, str]:
    """Liveness: the process is up. Deliberately touches no dependencies."""
    return {"status": "ok"}


@app.get("/readyz", tags=["ops"])
async def readyz() -> dict[str, str]:
    """Readiness: this instance can reach Redis, so updates can reach its viewers.

    Bidding is not checked. If it is down, viewers already connected keep their stream,
    and a viewer who cannot get a snapshot is told to try again.
    """
    if not await redis_ping():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="redis unavailable",
        )
    return {"status": "ready"}
