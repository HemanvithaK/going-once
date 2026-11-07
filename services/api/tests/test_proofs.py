"""Proofs for the outbox pipeline: each test is one promise, checked when things go wrong.

The happy path is covered elsewhere. These cover a broker outage in the middle of a
batch and old rows piling up. The viewer-side proofs (Redis outages, stale versions,
memory limits) moved to the realtime service along with the code they test.
"""

import asyncio
from contextlib import suppress
from datetime import timedelta

import httpx
from sqlalchemy import func, select, update

from app.core.db import SessionLocal
from app.models.outbox import OutboxEvent
from app.workers.relay import purge_sent_events, relay_once, run_relay
from tests.helpers import create_live_listing, make_user
from tests.test_relay import FakeProducer, published_count, total_published_count


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


async def test_events_made_during_a_kafka_outage_go_out_once_it_recovers(
    client: httpx.AsyncClient,
) -> None:
    seller = await make_user(client, "seller@example.com")
    ids = [(await create_live_listing(client, seller))["id"] for _ in range(3)]

    # The first two sends fail. Each failure rolls back the batch, so nothing is marked sent.
    producer = FlakyProducer(outage_sends=2)
    task = asyncio.create_task(run_relay(producer, poll_seconds=0.02))
    try:
        # Wait for the commit, not just the sends. A send can land just before its batch
        # commits, and cancelling then would roll the batch back.
        for _ in range(750):  # up to fifteen seconds; the test is about order, not speed
            if await total_published_count() == 3:
                break
            await asyncio.sleep(0.02)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    # Failed sends are never recorded, so the successful run is the whole batch, once each.
    delivered = sorted(key for _, key, _ in producer.sent)
    assert delivered == sorted(ids), f"delivered so far: {delivered}"
    assert await total_published_count() == 3


async def test_purge_deletes_old_sent_rows_and_nothing_else(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    old = (await create_live_listing(client, seller))["id"]
    recent = (await create_live_listing(client, seller))["id"]
    await relay_once(FakeProducer())  # sends both, so both rows are marked sent
    unsent = (await create_live_listing(client, seller))["id"]  # stays unsent

    # The first row was sent two days ago. The second was sent just now.
    async with SessionLocal() as session, session.begin():
        await session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.partition_key == old)
            .values(published_at=func.clock_timestamp() - timedelta(days=2))
        )

    assert await purge_sent_events(older_than=timedelta(days=1)) == 1

    async with SessionLocal() as session:
        remaining = set(await session.scalars(select(OutboxEvent.partition_key)))
    assert remaining == {recent, unsent}
    assert await published_count(recent) == 1
