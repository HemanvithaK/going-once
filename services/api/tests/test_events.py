import json
import uuid
from datetime import timedelta

import httpx
from redis.asyncio.client import PubSub
from sqlalchemy import func, update

from app.core.db import SessionLocal
from app.core.events import listing_channel
from app.core.redis import redis_client
from app.models.listing import Listing
from app.services.closing import close_all_due
from tests.helpers import create_live_listing, make_user, place_bid


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


async def set_deadline_past(listing_id: str) -> None:
    async with SessionLocal() as session, session.begin():
        await session.execute(
            update(Listing)
            .where(Listing.id == uuid.UUID(listing_id))
            .values(ends_at=func.clock_timestamp() - timedelta(seconds=1))
        )


async def test_an_accepted_bid_publishes_the_new_state(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00")
    lid = listing["id"]

    pubsub = await subscribe(listing_channel(lid))
    try:
        response = await place_bid(client, lid, alice, "150.00", "event-key-001")
        assert response.status_code == 201, response.text

        event = await next_event(pubsub)
        assert event is not None
        assert event["type"] == "listing.updated"
        assert event["current_price"] == "100.00"
        assert event["status"] == "live"
    finally:
        await pubsub.aclose()


async def test_a_rejected_bid_publishes_nothing(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    bob = await make_user(client, "bob@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")
    lid = listing["id"]
    await place_bid(client, lid, alice, "120.00", "event-key-002")

    pubsub = await subscribe(listing_channel(lid))
    try:
        # Below the minimum of 105, so the API answers 409 and the transaction rolls back.
        response = await place_bid(client, lid, bob, "102.00", "event-key-003")
        assert response.status_code == 409

        assert await next_event(pubsub, wait_seconds=0.5) is None
    finally:
        await pubsub.aclose()


async def test_a_closed_auction_publishes_its_final_status(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    alice = await make_user(client, "alice@example.com")
    listing = await create_live_listing(client, seller)
    lid = listing["id"]
    await place_bid(client, lid, alice, "150.00", "event-key-004")
    await set_deadline_past(lid)

    pubsub = await subscribe(listing_channel(lid))
    try:
        assert await close_all_due() == 1

        event = await next_event(pubsub)
        assert event is not None
        assert event["status"] == "sold"
    finally:
        await pubsub.aclose()