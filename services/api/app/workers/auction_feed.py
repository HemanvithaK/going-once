"""Feed consumer: reads auction-updates and keeps the listing read model current.

It commits the Kafka offset only after the database transaction commits. A crash in
between means the message is read again, and applying it twice leaves the same state.
"""

import asyncio
from contextlib import suppress

from aiokafka import AIOKafkaConsumer
from auction_contracts.events import AUCTION_UPDATES_TOPIC
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.services.auction_feed import FeedResult, apply_auction_update

log = get_logger(__name__)
_settings = get_settings()

# Its own group, separate from the outcomes consumer and from realtime.
CONSUMER_GROUP = "core-api-listings"


async def start_consumer() -> AIOKafkaConsumer:
    """Keep trying until the broker answers, the same way the other consumers do."""
    while True:
        consumer = AIOKafkaConsumer(
            AUCTION_UPDATES_TOPIC,
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
) -> FeedResult:
    """Retry while the database is down. Skipping an update would leave a stale price."""
    while True:
        try:
            async with session_factory() as session, session.begin():
                result = await apply_auction_update(session, raw)
            log.info("auction_update_handled", result=result.value)
            return result
        except Exception as exc:
            log.warning("auction_update_failed", error=str(exc) or type(exc).__name__)
            await asyncio.sleep(1)


async def run_feed(session_factory: async_sessionmaker[AsyncSession] = SessionLocal) -> None:
    """Apply updates until this task is cancelled."""
    consumer = await start_consumer()
    try:
        async for message in consumer:
            await _apply_until_database_accepts(session_factory, message.value or b"")
            await consumer.commit()
    finally:
        await consumer.stop()


def main() -> None:
    configure_logging()
    asyncio.run(run_feed())


if __name__ == "__main__":
    main()
