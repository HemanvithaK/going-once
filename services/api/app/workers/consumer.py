"""Consumer: reads listing events from Kafka and forwards them to Redis.

The realtime hub listens on Redis, so this is the step that connects Kafka to
the viewers. Every listing's events share one key, so they land in one partition
and arrive in version order.

The consumer commits an offset only after Redis has accepted the event. A crash
before the commit means the event is read again. A repeat is harmless: each
message carries the full state and its version.
"""

import asyncio
import json
from collections import OrderedDict
from contextlib import suppress
from typing import Any

from aiokafka import AIOKafkaConsumer

from app.core.config import get_settings
from app.core.events import LISTING_EVENTS_TOPIC, listing_channel
from app.core.logging import configure_logging, get_logger
from app.core.redis import close_redis, redis_client

log = get_logger(__name__)
_settings = get_settings()

# Every consumer process in this group shares the topic's partitions.
CONSUMER_GROUP = "realtime"
# Memory bound for the version map. Forgetting a listing only costs a duplicate forward.
MAX_TRACKED_LISTINGS = 10_000


class Forwarder:
    """Sends each event to its listing's Redis channel, unless a newer one already went out."""

    def __init__(self, max_listings: int = MAX_TRACKED_LISTINGS) -> None:
        self._max_listings = max_listings
        # The newest version this process has forwarded, per listing, least recently
        # used first. An older version that arrives later is stale, so it is skipped.
        self._latest: OrderedDict[str, int] = OrderedDict()

    async def handle(self, raw: bytes) -> bool:
        """Forward one event. Returns False when it was skipped."""
        try:
            event: dict[str, Any] = json.loads(raw)
            listing_id = str(event["listing_id"])
            version = int(event["version"])
        except (ValueError, KeyError, TypeError):
            # Retrying cannot fix a bad message, so it is skipped, not retried.
            log.error("event_malformed")
            return False

        if version <= self._latest.get(listing_id, 0):
            log.info("event_stale_skipped", listing_id=listing_id, version=version)
            return False

        # If Redis raises here, the error goes to the caller, which retries. The
        # version is recorded only after Redis accepted it.
        await redis_client.publish(listing_channel(listing_id), json.dumps(event))
        self._latest[listing_id] = version
        self._latest.move_to_end(listing_id)
        if len(self._latest) > self._max_listings:
            # Forget the listing that has gone longest without an event.
            self._latest.popitem(last=False)
        return True


async def start_consumer(group_id: str) -> AIOKafkaConsumer:
    """Keep trying until the broker answers, the same way the relay does."""
    while True:
        consumer = AIOKafkaConsumer(
            LISTING_EVENTS_TOPIC,
            bootstrap_servers=_settings.kafka_bootstrap_servers,
            group_id=group_id,
            # We commit by hand, after Redis has the event.
            enable_auto_commit=False,
            # A group seeing the topic for the first time starts at the oldest event,
            # so nothing sent before it joined is missed.
            auto_offset_reset="earliest",
        )
        try:
            await consumer.start()
            return consumer
        except Exception as exc:
            log.warning("kafka_not_ready", error=str(exc) or type(exc).__name__)
            with suppress(Exception):
                await consumer.stop()
            await asyncio.sleep(5)


async def _forward_until_redis_accepts(forwarder: Forwarder, raw: bytes) -> None:
    """Retry one event until Redis takes it. Skipping it would lose the update for good."""
    while True:
        try:
            await forwarder.handle(raw)
            return
        except Exception as exc:
            log.warning("forward_failed", error=str(exc) or type(exc).__name__)
            await asyncio.sleep(1)


async def run_consumer(forwarder: Forwarder, *, group_id: str = CONSUMER_GROUP) -> None:
    """Forward events until this task is cancelled."""
    consumer = await start_consumer(group_id)
    try:
        async for message in consumer:
            await _forward_until_redis_accepts(forwarder, message.value)
            await consumer.commit()
    finally:
        await consumer.stop()


async def _run() -> None:
    try:
        await run_consumer(Forwarder())
    finally:
        await close_redis()


def main() -> None:
    configure_logging()
    asyncio.run(_run())


if __name__ == "__main__":
    main()