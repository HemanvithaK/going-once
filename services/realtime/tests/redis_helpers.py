from redis.asyncio.client import PubSub

from app.core.redis import redis_client


async def subscribe(channel: str) -> PubSub:
    """Subscribe and wait for Redis to confirm, so no message can slip past us."""
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channel)
    await pubsub.get_message(ignore_subscribe_messages=False, timeout=2.0)
    return pubsub


async def next_message(pubsub: PubSub, wait_seconds: float = 2.0) -> str | None:
    """The next message's text, or None if nothing arrives in time."""
    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=wait_seconds)
    if message is None:
        return None
    data: str = message["data"]
    return data
