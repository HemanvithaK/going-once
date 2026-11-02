"""Auction sync: reads listing-events and keeps this service's auctions table current.

Each message is applied in its own transaction. The Kafka offset is committed only
after that transaction commits. A crash in between means the message is read again,
and applying it twice does nothing, because the version check ignores the repeat.
"""

import asyncio
from contextlib import suppress

from aiokafka import AIOKafkaConsumer
from auction_contracts.events import LISTING_TOPIC
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.services.auction_state import ApplyOutcome, apply_listing_event

log = get_logger(__name__)
_settings = get_settings()

# Its own group, so bidding and the core API each read the topic independently.
CONSUMER_GROUP = "bidding"


async def process_message(
    session_factory: async_sessionmaker[AsyncSession],
    raw: bytes | str,
) -> ApplyOutcome:
    """Apply one message in one transaction. Database errors propagate, so the caller retries."""
    async with session_factory() as session, session.begin():
        outcome = await apply_listing_event(session, raw)
    log.info("listing_event_handled", outcome=outcome.value)
    return outcome


async def start_consumer() -> AIOKafkaConsumer:
    """Keep trying until the broker answers, the same way the other consumers do."""
    while True:
        consumer = AIOKafkaConsumer(
            LISTING_TOPIC,
            bootstrap_servers=_settings.kafka_bootstrap_servers,
            group_id=CONSUMER_GROUP,
            enable_auto_commit=False,
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


async def _apply_until_database_accepts(
    session_factory: async_sessionmaker[AsyncSession],
    raw: bytes | str,
) -> None:
    """Retry while the database is down. Skipping the message would lose the auction for good."""
    while True:
        try:
            await process_message(session_factory, raw)
            return
        except Exception as exc:
            log.warning("apply_failed", error=str(exc) or type(exc).__name__)
            await asyncio.sleep(1)


async def run_sync(session_factory: async_sessionmaker[AsyncSession] = SessionLocal) -> None:
    """Apply events until this task is cancelled."""
    consumer = await start_consumer()
    try:
        async for message in consumer:
            await _apply_until_database_accepts(session_factory, message.value or b"")
            await consumer.commit()
    finally:
        await consumer.stop()


def main() -> None:
    configure_logging()
    asyncio.run(run_sync())


if __name__ == "__main__":
    main()
