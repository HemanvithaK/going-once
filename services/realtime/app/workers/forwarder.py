"""Forwarder: reads auction-updates from Kafka and publishes each one to Redis.

Every server process listens on Redis, so this is the step that connects Kafka to the
viewers. Every auction's updates share one key, so they land in one partition and arrive
in version order.

The consumer commits an offset only after Redis has accepted the update. A crash before
the commit means the update is read again. A repeat is harmless: each message carries the
full public state and its version.

Only auction.updated is ever forwarded. Anything else on the topic is dropped, so private
data cannot reach a browser even if it is sent to this topic by mistake.
"""

import asyncio
from collections import OrderedDict
from contextlib import suppress

from aiokafka import AIOKafkaConsumer
from auction_contracts.events import AUCTION_UPDATES_TOPIC, AuctionUpdated, parse_event
from pydantic import ValidationError

from app.core.channels import auction_channel
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.redis import close_redis, redis_client

log = get_logger(__name__)
_settings = get_settings()

# Every forwarder process in this group shares the topic's partitions.
CONSUMER_GROUP = "realtime-viewers"
# Memory bound for the version map. Forgetting an auction only costs a duplicate forward.
MAX_TRACKED_AUCTIONS = 10_000


class Forwarder:
    """Publishes each update to its auction's channel, unless a newer one already went out."""

    def __init__(self, max_auctions: int = MAX_TRACKED_AUCTIONS) -> None:
        self._max_auctions = max_auctions
        # The newest version this process has forwarded, per auction, least recently used
        # first. An older version that arrives later is stale, so it is skipped.
        self._latest: OrderedDict[str, int] = OrderedDict()

    async def handle(self, raw: bytes | str) -> bool:
        """Forward one message. Returns False when it was skipped."""
        try:
            event = parse_event(raw)
        except ValidationError:
            # Retrying cannot fix a bad message, so it is skipped, not retried.
            log.error("event_malformed")
            return False

        if not isinstance(event, AuctionUpdated):
            log.warning("event_not_public_skipped", event_type=event.type)
            return False

        listing_id = str(event.listing_id)
        if event.version <= self._latest.get(listing_id, -1):
            log.info("event_stale_skipped", listing_id=listing_id, version=event.version)
            return False

        # If Redis raises here, the error goes to the caller, which retries. The version
        # is recorded only after Redis accepted the message.
        await redis_client.publish(auction_channel(listing_id), event.model_dump_json())
        self._latest[listing_id] = event.version
        self._latest.move_to_end(listing_id)
        if len(self._latest) > self._max_auctions:
            # Forget the auction that has gone longest without an update.
            self._latest.popitem(last=False)
        return True


async def start_consumer(group_id: str) -> AIOKafkaConsumer:
    """Keep trying until the broker answers, the same way the other consumers do."""
    while True:
        consumer = AIOKafkaConsumer(
            AUCTION_UPDATES_TOPIC,
            bootstrap_servers=_settings.kafka_bootstrap_servers,
            group_id=group_id,
            # We commit by hand, after Redis has the update.
            enable_auto_commit=False,
            # A group seeing the topic for the first time starts at the oldest update, so
            # nothing sent while it was still joining is missed. Replaying old updates is
            # harmless: a socket skips any version it has already passed.
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


async def forward_until_redis_accepts(forwarder: Forwarder, raw: bytes | str) -> None:
    """Retry one message until Redis takes it. Skipping it would lose the update for good."""
    while True:
        try:
            await forwarder.handle(raw)
            return
        except Exception as exc:
            log.warning("forward_failed", error=str(exc) or type(exc).__name__)
            await asyncio.sleep(1)


async def run_consumer(forwarder: Forwarder, *, group_id: str = CONSUMER_GROUP) -> None:
    """Forward updates until this task is cancelled."""
    consumer = await start_consumer(group_id)
    try:
        async for message in consumer:
            await forward_until_redis_accepts(forwarder, message.value or b"")
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
