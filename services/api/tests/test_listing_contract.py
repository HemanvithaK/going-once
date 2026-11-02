"""The API's outbound events must match the contract that bidding reads.

If the API changes a field, these tests fail here, not later in a consumer.
"""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from auction_contracts.events import ListingPublished, parse_event
from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.events import LISTING_EVENTS_TOPIC
from app.models.outbox import OutboxEvent
from tests.helpers import create_listing, create_live_listing, make_user


async def listing_events_rows(listing_id: str) -> list[OutboxEvent]:
    async with SessionLocal() as session:
        result = await session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.partition_key == listing_id,
                OutboxEvent.topic == LISTING_EVENTS_TOPIC,
            )
        )
        return list(result)


async def test_a_published_listing_is_a_valid_contract_event(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    listing = await create_live_listing(client, seller, start_price="100.00", bid_increment="5.00")

    rows = await listing_events_rows(listing["id"])
    assert len(rows) == 1

    event = parse_event(json.dumps(rows[0].payload))
    assert isinstance(event, ListingPublished)
    assert str(event.listing_id) == listing["id"]
    assert event.version == 0
    assert event.start_price == Decimal("100.00")
    assert event.bid_increment == Decimal("5.00")
    assert event.reserve_price is None


async def test_a_scheduled_listing_publishes_nothing_to_bidding_yet(
    client: httpx.AsyncClient,
) -> None:
    """Bidding has no start time in the contract, so it only hears about live listings."""
    seller = await make_user(client, "seller@example.com")
    future = datetime.now(UTC) + timedelta(hours=2)
    listing = await create_listing(client, seller, starts_at=future.isoformat())

    response = await client.post(f"/listings/{listing['id']}/publish", headers=seller)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "scheduled"
    assert await listing_events_rows(listing["id"]) == []


async def test_the_publish_event_has_exactly_the_contract_fields(
    client: httpx.AsyncClient,
) -> None:
    """A field the contract does not know would be dropped silently. Catch it here instead."""
    seller = await make_user(client, "seller@example.com")
    listing = await create_live_listing(client, seller)
    rows = await listing_events_rows(listing["id"])
    assert set(rows[0].payload) == set(ListingPublished.model_fields) | {"type", "schema_version"}
