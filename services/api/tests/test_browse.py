import uuid
from datetime import timedelta

import httpx
from sqlalchemy import func, update

from app.core.db import SessionLocal
from app.models.listing import Listing
from tests.helpers import create_live_listing, make_user


async def set_deadline(listing_id: str, seconds_from_now: int) -> None:
    async with SessionLocal() as session, session.begin():
        await session.execute(
            update(Listing)
            .where(Listing.id == uuid.UUID(listing_id))
            .values(ends_at=func.clock_timestamp() + timedelta(seconds=seconds_from_now))
        )


async def test_an_auction_past_its_deadline_is_hidden_from_browse(
    client: httpx.AsyncClient,
) -> None:
    seller = await make_user(client, "seller@example.com")
    listing = await create_live_listing(client, seller)
    lid = listing["id"]
    await set_deadline(lid, seconds_from_now=-1)

    response = await client.get("/listings")
    shown = [item["id"] for item in response.json()["items"]]
    assert lid not in shown
