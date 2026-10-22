"""One Redis subscription per listing per API instance, shared by its viewers.

Without this, every open socket holds its own Redis connection. With it, the
first viewer of a listing subscribes once, and every later viewer just joins
the same list. Each viewer has a bounded queue. A viewer who stops reading
fills its queue and is dropped, so it cannot slow the others or use memory
without limit.
"""

import asyncio
from contextlib import suppress

from redis.asyncio.client import PubSub

from app.core.logging import get_logger
from app.core.redis import redis_client

log = get_logger(__name__)

# How far a viewer may fall behind before it is dropped. Every message carries
# the full listing state, so a dropped viewer loses nothing once it reconnects.
SUBSCRIBER_QUEUE_LIMIT = 100


class Subscriber:
    """One viewer's place in the hub. The queue holds JSON strings. None means dropped."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_LIMIT)
        self.dropped = False


class ListingHub:
    def __init__(self) -> None:
        self._viewers: dict[str, set[Subscriber]] = {}
        self._pubsub: PubSub | None = None
        self._reader: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def join(self, channel: str) -> Subscriber:
        """Add a viewer. The first viewer of a channel subscribes it in Redis."""
        subscriber = Subscriber()
        async with self._lock:
            pubsub = self._pubsub
            if pubsub is None:
                pubsub = redis_client.pubsub()
                self._pubsub = pubsub
                self._reader = asyncio.create_task(self._read_forever(pubsub))
            viewers = self._viewers.setdefault(channel, set())
            if not viewers:
                await pubsub.subscribe(channel)
            viewers.add(subscriber)
        return subscriber

    async def leave(self, channel: str, subscriber: Subscriber) -> None:
        """Remove a viewer. The last viewer of a channel unsubscribes it in Redis."""
        async with self._lock:
            viewers = self._viewers.get(channel)
            if viewers is None:
                return
            viewers.discard(subscriber)
            if not viewers:
                del self._viewers[channel]
                if self._pubsub is not None:
                    await self._pubsub.unsubscribe(channel)

    def deliver(self, channel: str, data: str) -> None:
        """Hand one message to every viewer of a channel. Never waits."""
        for subscriber in tuple(self._viewers.get(channel, ())):
            if subscriber.dropped:
                continue
            try:
                subscriber.queue.put_nowait(data)
            except asyncio.QueueFull:
                self._drop(channel, subscriber)

    def watcher_count(self, channel: str) -> int:
        return len(self._viewers.get(channel, ()))

    def _drop(self, channel: str, subscriber: Subscriber) -> None:
        """Throw away a viewer's backlog and leave a single None to say it was dropped."""
        subscriber.dropped = True
        while not subscriber.queue.empty():
            subscriber.queue.get_nowait()
        subscriber.queue.put_nowait(None)  # room is guaranteed: we just emptied the queue
        log.warning("viewer_dropped_too_slow", channel=channel)

    async def _read_forever(self, pubsub: PubSub) -> None:
        """Read Redis and fan each message out. A Redis error is logged, then retried."""
        while True:
            if not pubsub.subscribed:
                # Nobody is watching right now. Wait quietly until someone joins.
                await asyncio.sleep(0.1)
                continue
            try:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is not None:
                    self.deliver(message["channel"], message["data"])
            except Exception as exc:
                log.warning("hub_read_failed", error=str(exc))
                await asyncio.sleep(1.0)

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader
        if self._pubsub is not None:
            await self._pubsub.aclose()
        self._pubsub = None
        self._reader = None
        self._viewers.clear()


listing_hub = ListingHub()