"""One shared Redis connection per process.

Redis carries the auction events between API instances (pub/sub, Phase 6).
The client is created once and reused, because each client keeps its own
pool of connections.
"""

from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)
_settings = get_settings()

# Creating the client does not connect. Connections open on the first command,
# so importing this module never fails just because Redis is down.
redis_client: Redis = Redis.from_url(_settings.redis_url, decode_responses=True)


async def redis_ping() -> bool:
    """True if Redis answers. Never raises, so health checks can use it safely."""
    try:
        return bool(await redis_client.ping())
    except Exception as exc:
        log.warning("redis_ping_failed", error=str(exc))
        return False


async def close_redis() -> None:
    await redis_client.aclose()