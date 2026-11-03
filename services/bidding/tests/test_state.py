import uuid

import httpx
from auction_contracts.events import AUCTION_UPDATES_TOPIC, AuctionUpdated
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import OutboxEvent
from app.services.closing import close_all_due
from tests.helpers import make_due, open_auction, place


async def latest_update_event(listing_id: uuid.UUID) -> AuctionUpdated:
    """The newest auction-updates message queued for this auction."""
    async with SessionLocal() as session:
        rows = list(
            await session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.topic == AUCTION_UPDATES_TOPIC,
                    OutboxEvent.partition_key == str(listing_id),
                )
            )
        )
    assert rows, "no auction-updates event was queued"
    events = [AuctionUpdated.model_validate(row.payload) for row in rows]
    return max(events, key=lambda event: event.version)


async def test_an_unknown_auction_is_404(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/listings/{uuid.uuid4()}/state")
    assert response.status_code == 404


async def test_a_live_auction_shows_its_public_state_without_a_login(
    client: httpx.AsyncClient,
) -> None:
    listing_id, _ = await open_auction(start="100.00")

    response = await client.get(f"/listings/{listing_id}/state")  # no Authorization header

    assert response.status_code == 200
    state = response.json()
    assert state["type"] == "auction.updated"
    assert state["listing_id"] == str(listing_id)
    assert state["status"] == "live"
    assert state["current_price"] == "100.00"
    assert state["version"] == 0
    assert response.headers["cache-control"] == "no-store"


async def test_the_state_follows_bids_and_matches_the_latest_stream_event(
    client: httpx.AsyncClient,
) -> None:
    listing_id, _ = await open_auction(start="100.00", increment="5.00")
    await place(client, listing_id, uuid.uuid4(), "120.00", "alice-key-1")
    await place(client, listing_id, uuid.uuid4(), "150.00", "bob-key-01")

    response = await client.get(f"/listings/{listing_id}/state")
    state = AuctionUpdated.model_validate(response.json())

    # A viewer who joins now gets what the stream last said, so a snapshot and a
    # stream message can be ordered by their versions.
    assert str(state.current_price) == "125.00"
    assert state == await latest_update_event(listing_id)


async def test_a_settled_auction_shows_its_final_status(client: httpx.AsyncClient) -> None:
    listing_id, _ = await open_auction(start="100.00", increment="5.00")
    await place(client, listing_id, uuid.uuid4(), "150.00", "only-bid-01")
    await make_due(listing_id)
    assert await close_all_due() == 1

    response = await client.get(f"/listings/{listing_id}/state")

    assert response.json()["status"] == "sold"


async def test_the_state_never_carries_private_data(client: httpx.AsyncClient) -> None:
    seller = uuid.uuid4()
    bidder = uuid.uuid4()
    listing_id, _ = await open_auction(seller_id=seller, start="100.00", reserve="500.00")
    await place(client, listing_id, bidder, "700.00", "leader-key-1")

    response = await client.get(f"/listings/{listing_id}/state")

    # Exactly the contract's public fields: nothing was added that a viewer should not see.
    assert set(response.json()) == set(AuctionUpdated.model_fields)
    for secret in ("500.00", "700.00", str(seller), str(bidder), "reserve", "winner"):
        assert secret not in response.text, f"{secret!r} leaked into the public state"
