import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text, update

from app.core.db import SessionLocal, engine
from app.models.outbox import OutboxEvent
from app.workers.relay import RELAY_LOCK_KEY, purge_sent_events, relay_once, run_relay


class FakeProducer:
    """Records what would have gone to Kafka. Can fail on a chosen send."""

    def __init__(self, fail_on: int | None = None) -> None:
        self.sent: list[tuple[str, bytes]] = []
        self.fail_on = fail_on

    async def send_and_wait(self, topic: str, *, key: bytes, value: bytes) -> object:
        if self.fail_on is not None and len(self.sent) == self.fail_on:
            raise ConnectionError("broker refused the send")
        self.sent.append((topic, value))
        return None


def outbox_row(version: int, *, listing_id: uuid.UUID | None = None) -> OutboxEvent:
    return OutboxEvent(
        topic="auction-updates",
        partition_key=str(listing_id or uuid.uuid4()),
        payload={"version": version, "type": "auction.updated"},
    )


async def save(*rows: OutboxEvent) -> None:
    async with SessionLocal() as session:
        session.add_all(rows)
        await session.commit()


async def unsent_count() -> int:
    async with SessionLocal() as session:
        return (
            await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.published_at.is_(None))
            )
            or 0
        )


def versions_sent(producer: FakeProducer) -> list[int]:
    return [json.loads(value)["version"] for _, value in producer.sent]


async def test_events_go_out_in_version_order_and_are_marked_sent() -> None:
    listing_id = uuid.uuid4()
    await save(outbox_row(2, listing_id=listing_id), outbox_row(0, listing_id=listing_id))
    await save(outbox_row(1, listing_id=listing_id))
    producer = FakeProducer()

    sent = await relay_once(producer)

    assert sent == 3
    assert versions_sent(producer) == [0, 1, 2]
    assert await unsent_count() == 0


async def test_a_failed_send_leaves_the_whole_batch_unsent_and_the_next_run_sends_it() -> None:
    await save(outbox_row(0), outbox_row(1), outbox_row(2))

    with pytest.raises(ConnectionError):
        await relay_once(FakeProducer(fail_on=1))
    assert await unsent_count() == 3

    producer = FakeProducer()
    assert await relay_once(producer) == 3
    assert versions_sent(producer) == [0, 1, 2]


async def test_purge_removes_only_old_sent_rows() -> None:
    old_sent = outbox_row(0)
    recent_sent = outbox_row(1)
    unsent = outbox_row(2)
    await save(old_sent, recent_sent, unsent)
    now = datetime.now(UTC)
    async with SessionLocal() as session, session.begin():
        await session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == old_sent.id)
            .values(published_at=now - timedelta(hours=25))
        )
        await session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == recent_sent.id)
            .values(published_at=now - timedelta(hours=1))
        )

    removed = await purge_sent_events()

    assert removed == 1
    assert await unsent_count() == 1


async def test_a_second_relay_does_nothing_while_the_first_holds_the_lock() -> None:
    await save(outbox_row(0))
    async with engine.connect() as holder:
        locked = await holder.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": RELAY_LOCK_KEY}
        )
        assert locked

        producer = FakeProducer()
        await run_relay(producer, once=True)

        assert producer.sent == []
        assert await unsent_count() == 1
        await holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": RELAY_LOCK_KEY})
