import asyncio
import json
import os
import uuid
from contextlib import suppress

import httpx
import pytest
from redis.asyncio.client import PubSub

from app.core.events import listing_channel
from app.core.redis import redis_client
from app.services.auction_feed import FeedResult
from app.services.settlement import OutcomeResult
from app.workers.consumer import Forwarder, run_consumer
from app.workers.relay import make_producer, relay_once
from tests.helpers import create_live_listing, feed_auction_update, make_user, settle, user_id
from tests.pipeline import deliver_outbox

requires_broker = pytest.mark.skipif(
    not os.environ.get("KAFKA_BOOTSTRAP_SERVERS"),
    reason="needs a running Kafka broker: set KAFKA_BOOTSTRAP_SERVERS",
)


async def subscribe(channel: str) -> PubSub:
    """Subscribe and wait for Redis to confirm, so no event can slip past us."""
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channel)
    await pubsub.get_message(ignore_subscribe_messages=False, timeout=2.0)
    return pubsub


async def next_event(pubsub: PubSub, wait_seconds: float = 2.0) -> dict[str, object] | None:
    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=wait_seconds)
    if message is None:
        return None
    event: dict[str, object] = json.loads(message["data"])
    return event


def fake_event(listing_id: str, version: int) -> bytes:
    return json.dumps(
        {"type": "listing.updated", "listing_id": listing_id, "version": version}
    ).encode()


async def test_a_feed_update_reaches_its_listing_channel(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00")
    lid = listing["id"]

    pubsub = await subscribe(listing_channel(lid))
    try:
        assert await feed_auction_update(lid, price="120.00") is FeedResult.APPLIED

        await deliver_outbox()  # outbox -> relay -> forwarder -> Redis

        event = await next_event(pubsub)
        assert event is not None
        assert event["type"] == "listing.updated"
        assert event["version"] == 1
        assert event["current_price"] == "120.00"
        assert event["status"] == "live"
    finally:
        await pubsub.aclose()


async def test_an_update_for_an_unknown_listing_publishes_nothing(
    client: httpx.AsyncClient,
) -> None:
    seller = await make_user(client, "seller@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00")
    lid = listing["id"]
    await deliver_outbox()  # the publish goes out before we start listening

    pubsub = await subscribe(listing_channel(lid))
    try:
        await feed_auction_update(str(uuid.uuid4()), price="102.00")
        await deliver_outbox()

        assert await next_event(pubsub, wait_seconds=0.5) is None
    finally:
        await pubsub.aclose()


async def test_a_settled_auction_publishes_its_final_status(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    await make_user(client, "alice@example.com")
    listing = await create_live_listing(client, seller)
    lid = listing["id"]
    await feed_auction_update(lid, price="150.00")
    await deliver_outbox()  # the update goes out before we start listening

    pubsub = await subscribe(listing_channel(lid))
    try:
        outcome = await settle(
            lid,
            status="sold",
            winner=await user_id("alice@example.com"),
            price="150.00",
        )
        assert outcome is OutcomeResult.RECORDED
        await deliver_outbox()

        event = await next_event(pubsub)
        assert event is not None
        assert event["status"] == "sold"
    finally:
        await pubsub.aclose()


async def test_an_older_or_repeated_version_is_not_forwarded_again() -> None:
    lid = str(uuid.uuid4())
    forwarder = Forwarder()

    pubsub = await subscribe(listing_channel(lid))
    try:
        assert await forwarder.handle(fake_event(lid, 2)) is True
        assert await forwarder.handle(fake_event(lid, 1)) is False  # older than what went out
        assert await forwarder.handle(fake_event(lid, 2)) is False  # a repeat
        assert await forwarder.handle(fake_event(lid, 3)) is True

        first = await next_event(pubsub)
        second = await next_event(pubsub)
        assert first is not None and first["version"] == 2
        assert second is not None and second["version"] == 3
        assert await next_event(pubsub, wait_seconds=0.3) is None
    finally:
        await pubsub.aclose()


async def test_a_malformed_event_is_skipped_not_retried() -> None:
    forwarder = Forwarder()
    assert await forwarder.handle(b"not json") is False
    assert await forwarder.handle(b'{"version": 1}') is False  # no listing_id


@requires_broker
async def test_an_event_crosses_a_real_kafka_broker_to_redis(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")
    lid = listing["id"]
    await feed_auction_update(lid, price="120.00")

    pubsub = await subscribe(listing_channel(lid))
    # A new group name each run. The group starts at the oldest event on the topic,
    # so it does not matter whether the consumer joins before or after the send.
    consumer_task = asyncio.create_task(run_consumer(Forwarder(), group_id=f"test-{uuid.uuid4()}"))
    producer = make_producer()
    await producer.start()
    try:
        # The publish and the update are both unsent, so two rows go to Kafka.
        assert await relay_once(producer) == 2
        event = await next_event(pubsub, wait_seconds=20)
    finally:
        await producer.stop()
        consumer_task.cancel()
        with suppress(asyncio.CancelledError):
            await consumer_task
        await pubsub.aclose()

    assert event is not None
    assert event["listing_id"] == lid
    assert event["version"] == 1
