import asyncio
import json
from contextlib import suppress
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, text

from app.core.db import SessionLocal, engine
from app.core.events import LISTING_EVENTS_TOPIC
from app.models.outbox import OutboxEvent
from app.workers.relay import RELAY_LOCK_KEY, relay_once, run_relay
from tests.helpers import create_live_listing, make_user


class FakeProducer:
    """Records what would have gone to Kafka. Can fail on a chosen send, like a broker outage."""

    def __init__(self, fail_on_send: int | None = None) -> None:
        self.sent: list[tuple[str, str, dict[str, Any]]] = []
        self._fail_on_send = fail_on_send

    async def send_and_wait(self, topic: str, *, key: bytes, value: bytes) -> object:
        if len(self.sent) == self._fail_on_send:
            raise ConnectionError("broker went away")
        payload: dict[str, Any] = json.loads(value)
        self.sent.append((topic, key.decode(), payload))
        return None


async def total_published_count() -> int:
    async with SessionLocal() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.published_at.is_not(None))
        )
    return int(count or 0)


async def published_count(listing_id: str) -> int:
    async with SessionLocal() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(
                OutboxEvent.partition_key == listing_id,
                OutboxEvent.published_at.is_not(None),
            )
        )
    return int(count or 0)


async def publish_listings(client: httpx.AsyncClient, count: int) -> list[str]:
    """Publish `count` listings, which queues one listing-events row for each."""
    seller = await make_user(client, "seller@example.com")
    ids = []
    for _ in range(count):
        listing = await create_live_listing(client, seller)
        ids.append(listing["id"])
    return ids


async def test_relay_sends_each_published_listing_and_marks_the_rows_sent(
    client: httpx.AsyncClient,
) -> None:
    first, second = await publish_listings(client, 2)

    producer = FakeProducer()
    assert await relay_once(producer) == 2

    # Rows have random ids and each listing has one row, so there is no order across listings.
    assert {(topic, key, payload["type"]) for topic, key, payload in producer.sent} == {
        (LISTING_EVENTS_TOPIC, first, "listing.published"),
        (LISTING_EVENTS_TOPIC, second, "listing.published"),
    }
    assert await published_count(first) == 1
    assert await published_count(second) == 1
    assert await relay_once(producer) == 0  # nothing left to send


async def test_a_backlog_goes_out_oldest_first(client: httpx.AsyncClient) -> None:
    ids = await publish_listings(client, 5)  # published one after another

    producer = FakeProducer()
    assert await relay_once(producer) == 5

    # Outbox ids are random, so only the creation time can put these in publish order.
    assert [key for _, key, _ in producer.sent] == ids


async def test_a_failed_batch_is_sent_again_in_full(client: httpx.AsyncClient) -> None:
    ids = await publish_listings(client, 3)

    # The third send fails. The first two were accepted, but the batch rolls back.
    failing = FakeProducer(fail_on_send=2)
    with pytest.raises(ConnectionError):
        await relay_once(failing)
    assert len(failing.sent) == 2
    assert await total_published_count() == 0  # nothing was marked sent

    # The next run sends the whole batch again.
    healthy = FakeProducer()
    assert await relay_once(healthy) == 3
    assert sorted(key for _, key, _ in healthy.sent) == sorted(ids)
    assert await total_published_count() == 3


async def test_a_second_relay_does_nothing_while_the_first_holds_the_lock(
    client: httpx.AsyncClient,
) -> None:
    await publish_listings(client, 1)

    async with engine.connect() as holder:
        assert (
            await holder.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": RELAY_LOCK_KEY})
            is True
        )
        try:
            producer = FakeProducer()
            # Returns at once, because the lock is taken. Nothing is sent.
            await asyncio.wait_for(run_relay(producer), timeout=5)
            assert producer.sent == []
        finally:
            await holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": RELAY_LOCK_KEY})


async def test_a_running_relay_sends_new_events_as_they_arrive(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")

    producer = FakeProducer()
    task = asyncio.create_task(run_relay(producer, poll_seconds=0.05))
    try:
        listing = await create_live_listing(client, seller)
        for _ in range(100):  # up to about two seconds
            if producer.sent:
                break
            await asyncio.sleep(0.02)
        assert [key for _, key, _ in producer.sent] == [listing["id"]]
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
