"""Relay: sends outbox events to Kafka, in order, then marks them sent.

Only one relay runs at a time. It takes a Postgres advisory lock, which is a
named lock held by a database connection. A second relay, such as a second
container, finds the lock taken and does nothing. If the running relay dies,
its connection closes, the lock is released, and the next relay takes over.

Delivery is at-least-once. If the relay crashes after Kafka accepts an event but
before its batch commits, the event is sent again. Consumers must tolerate
repeats. Each event carries the listing's version, so a consumer can ignore
anything it has already applied.
"""

import argparse
import asyncio
import json
from contextlib import suppress
from typing import Protocol

from aiokafka import AIOKafkaProducer
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.db import SessionLocal, engine
from app.core.logging import configure_logging, get_logger
from app.models.outbox import OutboxEvent

log = get_logger(__name__)
_settings = get_settings()

# Any fixed number works. It names "the relay" for the advisory lock.
RELAY_LOCK_KEY = 704_102_201
BATCH_SIZE = 100
RELAY_POLL_SECONDS = 1.0


class Sender(Protocol):
    """The part of a Kafka producer the relay uses. Tests pass a fake that matches it."""

    async def send_and_wait(self, topic: str, *, key: bytes, value: bytes) -> object: ...


def make_producer() -> AIOKafkaProducer:
    return AIOKafkaProducer(
        bootstrap_servers=_settings.kafka_bootstrap_servers,
        # Idempotence stops the broker keeping a duplicate caused by a producer
        # retry. acks="all" waits until every replica has the message.
        enable_idempotence=True,
        acks="all",
    )


async def start_producer() -> AIOKafkaProducer:
    """Keep trying until the broker answers. Events wait in the outbox meanwhile."""
    while True:
        producer = make_producer()
        try:
            await producer.start()
            return producer
        except Exception as exc:
            log.warning("kafka_not_ready", error=str(exc) or type(exc).__name__)
            with suppress(Exception):
                await producer.stop()
            await asyncio.sleep(5)


async def relay_once(
    producer: Sender,
    session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
    batch_size: int = BATCH_SIZE,
) -> int:
    """Send one batch of unsent events. Returns how many were sent.

    The batch is one transaction. Rows are marked sent only after every send in
    the batch succeeds. If any send fails, the transaction rolls back, and the
    whole batch goes out again next time.
    """
    async with session_factory() as session, session.begin():
        rows = list(
            await session.scalars(
                select(OutboxEvent)
                .where(OutboxEvent.published_at.is_(None))
                # Within one listing, versions must go out in order. Sorting by version
                # does that. It is safe because version N+1 cannot be committed before
                # version N, so the relay never sees N+1 without N.
                .order_by(OutboxEvent.payload["version"].as_integer(), OutboxEvent.id)
                .limit(batch_size)
                .with_for_update()
            )
        )
        if not rows:
            return 0

        for row in rows:
            await producer.send_and_wait(
                row.topic,
                key=row.partition_key.encode(),
                value=json.dumps(row.payload).encode(),
            )

        await session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id.in_([row.id for row in rows]))
            .values(published_at=func.clock_timestamp())
        )
    log.info("events_relayed", count=len(rows))
    return len(rows)


async def run_relay(
    producer: Sender,
    *,
    poll_seconds: float = RELAY_POLL_SECONDS,
    once: bool = False,
) -> None:
    """Send events for as long as this process holds the relay lock.

    With once=True, send a single batch and return.
    """
    async with engine.connect() as lock_conn:
        acquired = await lock_conn.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": RELAY_LOCK_KEY}
        )
        if not acquired:
            log.info("relay_lock_held_elsewhere")
            return

        try:
            if once:
                sent = await relay_once(producer)
                log.info("relay_once_done", sent=sent)
                return
            while True:
                try:
                    sent = await relay_once(producer)
                except Exception as exc:
                    # Kafka is down or refused a send. The events stay in the outbox.
                    log.warning("relay_cycle_failed", error=str(exc) or type(exc).__name__)
                    sent = 0
                if sent == 0:
                    await asyncio.sleep(poll_seconds)
        finally:
            await lock_conn.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": RELAY_LOCK_KEY}
            )


async def _run(once: bool) -> None:
    producer = await start_producer()
    try:
        await run_relay(producer, once=once)
    finally:
        await producer.stop()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Send outbox events to Kafka.")
    parser.add_argument("--once", action="store_true", help="send one batch and exit")
    args = parser.parse_args()
    configure_logging()
    asyncio.run(_run(once=args.once))


if __name__ == "__main__":
    main()