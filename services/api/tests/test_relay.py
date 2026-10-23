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
from tests.helpers import create_live_listing, make_user, place_bid


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


async def test_relay_sends_each_listing_in_version_order_and_marks_rows_sent(
    client: httpx.AsyncClient,
) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    bob = await make_user(client, "bob@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")
    lid = listing["id"]
    await place_bid(client, lid, alice, "120.00", "relay-alice")
    await place_bid(client, lid, bob, "150.00", "relay-bob")

    producer = FakeProducer()
    assert await relay_once(producer) == 2

    assert [(topic, key, payload["version"]) for topic, key, payload in producer.sent] == [
        (LISTING_EVENTS_TOPIC, lid, 1),
        (LISTING_EVENTS_TOPIC, lid, 2),
    ]
    assert await published_count(lid) == 2
    assert await relay_once(producer) == 0  # nothing left to send


async def test_a_failed_batch_is_sent_again_in_full(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    bob = await make_user(client, "bob@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")
    lid = listing["id"]
    await place_bid(client, lid, alice, "120.00", "fail-alice")
    await place_bid(client, lid, bob, "150.00", "fail-bob")
    await place_bid(client, lid, alice, "160.00", "fail-alice-2")

    # The third send fails. The first two were accepted, but the batch rolls back.
    failing = FakeProducer(fail_on_send=2)
    with pytest.raises(ConnectionError):
        await relay_once(failing)
    assert [payload["version"] for _, _, payload in failing.sent] == [1, 2]
    assert await published_count(lid) == 0  # nothing was marked sent

    # The next run sends the whole batch again. Versions 1 and 2 go out twice.
    healthy = FakeProducer()
    assert await relay_once(healthy) == 3
    assert [payload["version"] for _, _, payload in healthy.sent] == [1, 2, 3]
    assert await published_count(lid) == 3


async def test_a_second_relay_does_nothing_while_the_first_holds_the_lock(
    client: httpx.AsyncClient,
) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")
    await place_bid(client, listing["id"], alice, "120.00", "lock-alice")

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
    alice = await make_user(client, "alice@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")
    lid = listing["id"]

    producer = FakeProducer()
    task = asyncio.create_task(run_relay(producer, poll_seconds=0.05))
    try:
        await place_bid(client, lid, alice, "120.00", "live-relay-alice")
        for _ in range(100):  # up to about two seconds
            if producer.sent:
                break
            await asyncio.sleep(0.02)
        assert [payload["version"] for _, _, payload in producer.sent] == [1]
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task