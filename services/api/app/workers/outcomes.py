"""Outcomes consumer: reads auction-outcomes and records each result as an order or a non-sale.

It commits the Kafka offset only after the database transaction commits. A crash in
between means the message is read again, and the repeat is ignored.
"""

import asyncio
from contextlib import suppress

from aiokafka import AIOKafkaConsumer
from auction_contracts.events import AUCTION_OUTCOMES_TOPIC
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.services.settlement import OutcomeResult, record_outcome

log = get_logger(__name__)
_settings = get_settings()

# Its own group, so the core API records outcomes independently of any other reader.
CONSUMER_GROUP = "core-api-orders"


async def start_consumer() -> AIOKafkaConsumer:
    """Keep trying until the broker answers, the same way the other consumers do."""
    while True:
        consumer = AIOKafkaConsumer(
            AUCTION_OUTCOMES_TOPIC,
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


async def _record_until_database_accepts(
    session_factory: async_sessionmaker[AsyncSession],
    raw: bytes | str,
) -> OutcomeResult:
    """Retry while the database is down. Skipping the outcome would lose the sale for good."""
    while True:
        try:
            async with session_factory() as session, session.begin():
                outcome = await record_outcome(session, raw)
            log.info("outcome_handled", outcome=outcome.value)
            return outcome
        except Exception as exc:
            log.warning("outcome_failed", error=str(exc) or type(exc).__name__)
            await asyncio.sleep(1)


async def run_outcomes(session_factory: async_sessionmaker[AsyncSession] = SessionLocal) -> None:
    """Record outcomes until this task is cancelled."""
    consumer = await start_consumer()
    try:
        async for message in consumer:
            await _record_until_database_accepts(session_factory, message.value or b"")
            await consumer.commit()
    finally:
        await consumer.stop()


def main() -> None:
    configure_logging()
    asyncio.run(run_outcomes())


if __name__ == "__main__":
    main()
