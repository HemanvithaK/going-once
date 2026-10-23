"""Part E proofs: each test is one promise the outbox pipeline makes, checked directly.

Earlier tests cover the happy path. These cover what happens when things go wrong
in the middle: a broker outage, a Redis outage, two listings interleaved, old rows
piling up, and memory limits.
"""

import asyncio
import uuid
from contextlib import suppress
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select, update

from app.core.db import SessionLocal
from app.core.events import listing_channel
from app.core.redis import redis_client
from app.models.outbox import OutboxEvent
from app.workers.consumer import Forwarder, _forward_until_redis_accepts
from app.workers.relay import purge_sent_events, relay_once, run_relay
from tests.helpers import create_live_listing, make_user, place_bid
from tests.pipeline import LoopbackBus
from tests.test_consumer import fake_event, next_event, subscribe
from tests.test_relay import FakeProducer, published_count


class FlakyProducer(FakeProducer):
    """Fails its first `outage_sends` sends, like a broker that is down and then comes back."""

    def __init__(self, outage_sends: int) -> None:
        super().__init__()
        self._outage_sends = outage_sends
        self._calls = 0

    async def send_and_wait(self, topic: str, *, key: bytes, value: bytes) -> object:
        self._calls += 1
        if self._calls <= self._outage_sends:
            raise ConnectionError("broker down")
        return await super().send_and_wait(topic, key=key, value=value)


async def test_two_listings_interleaved_still_go_out_in_order_for_each(
    client: httpx.AsyncClient,
) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    bob = await make_user(client, "bob@example.com")
    first = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")
    second = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")

    # Bids alternate between the two listings, so their versions interleave in the outbox.
    await place_bid(client, first["id"], alice, "120.00", "order-a1")
    await place_bid(client, second["id"], alice, "120.00", "order-b1")
    await place_bid(client, first["id"], bob, "150.00", "order-a2")
    await place_bid(client, second["id"], bob, "150.00", "order-b2")

    producer = FakeProducer()
    assert await relay_once(producer) == 4

    for listing in (first, second):
        versions = [payload["version"] for _, key, payload in producer.sent if key == listing["id"]]
        assert versions == [1, 2]


async def test_events_made_during_a_kafka_outage_go_out_once_it_recovers(
    client: httpx.AsyncClient,
) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    bob = await make_user(client, "bob@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")
    lid = listing["id"]
    await place_bid(client, lid, alice, "120.00", "outage-alice")
    await place_bid(client, lid, bob, "150.00", "outage-bob")
    await place_bid(client, lid, alice, "160.00", "outage-alice-2")

    # The first two sends fail. Each failure rolls back the batch, so nothing is marked sent.
    producer = FlakyProducer(outage_sends=2)
    task = asyncio.create_task(run_relay(producer, poll_seconds=0.02))
    try:
        # Wait for the commit, not just the sends. A send can land just before its batch
        # commits, and cancelling then would roll the batch back.
        for _ in range(750):  # up to fifteen seconds; the test is about order, not speed
            if await published_count(lid) == 3:
                break
            await asyncio.sleep(0.02)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    # Failed sends are never recorded, so the successful run is the whole batch, in order.
    delivered = [payload["version"] for _, _, payload in producer.sent]
    assert delivered == [1, 2, 3], f"delivered so far: {delivered}"
    assert await published_count(lid) == 3


async def test_a_redis_outage_delays_an_event_but_never_loses_it(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    listing = await create_live_listing(client, seller)
    lid = listing["id"]
    await place_bid(client, lid, alice, "120.00", "redis-alice")

    bus = LoopbackBus()
    await relay_once(bus)  # the event is now in the bus, marked sent in the outbox

    pubsub = await subscribe(listing_channel(lid))
    real_publish = redis_client.publish
    outages_left = 2

    async def flaky_publish(channel: str, message: str) -> int:
        nonlocal outages_left
        if outages_left > 0:
            outages_left -= 1
            raise ConnectionError("redis down")
        return await real_publish(channel, message)

    monkeypatch.setattr(redis_client, "publish", flaky_publish)
    try:
        for value in bus.values:
            await _forward_until_redis_accepts(Forwarder(), value)  # retries through the outage
        event = await next_event(pubsub)
    finally:
        await pubsub.aclose()

    assert outages_left == 0
    assert event is not None
    assert event["version"] == 1


async def test_purge_deletes_old_sent_rows_and_nothing_else(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    bob = await make_user(client, "bob@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")
    lid = listing["id"]
    await place_bid(client, lid, alice, "120.00", "purge-alice")
    await place_bid(client, lid, bob, "150.00", "purge-bob")
    await relay_once(FakeProducer())  # sends versions 1 and 2
    await place_bid(client, lid, alice, "160.00", "purge-alice-2")  # version 3 stays unsent

    # Version 1 was sent two days ago. Version 2 was sent just now.
    async with SessionLocal() as session, session.begin():
        await session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.payload["version"].as_integer() == 1)
            .values(published_at=func.clock_timestamp() - timedelta(days=2))
        )

    assert await purge_sent_events(older_than=timedelta(days=1)) == 1

    async with SessionLocal() as session:
        remaining = list(
            await session.scalars(select(OutboxEvent).where(OutboxEvent.partition_key == lid))
        )
    assert sorted(row.payload["version"] for row in remaining) == [2, 3]


async def test_a_forgotten_listing_is_forwarded_again_rather_than_lost() -> None:
    a, b, c = (str(uuid.uuid4()) for _ in range(3))
    forwarder = Forwarder(max_listings=2)

    assert await forwarder.handle(fake_event(a, 5)) is True
    assert await forwarder.handle(fake_event(b, 1)) is True
    assert await forwarder.handle(fake_event(c, 1)) is True  # the map is full: a is forgotten
    assert await forwarder.handle(fake_event(a, 4)) is True  # forwarded again, not dropped
    assert await forwarder.handle(fake_event(c, 1)) is False  # c is still remembered