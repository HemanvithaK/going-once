import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.user import User
from app.services.auction_feed import FeedResult, apply_auction_update
from app.services.settlement import OutcomeResult, record_outcome

PASSWORD = "a-long-enough-password"


async def make_user(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    """Register and log in, returning an Authorization header."""
    await client.post(
        "/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": email.split("@")[0]},
    )
    response = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def user_id(email: str) -> uuid.UUID:
    async with SessionLocal() as session:
        found = await session.scalar(select(User.id).where(User.email == email))
    assert found is not None
    return found


def listing_body(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    body: dict[str, Any] = {
        "title": "Vintage camera",
        "description": "A nice one.",
        "start_price": "100.00",
        "bid_increment": "5.00",
        "starts_at": (now - timedelta(minutes=1)).isoformat(),
        "ends_at": (now + timedelta(days=2)).isoformat(),
    }
    body.update(overrides)
    return body


async def create_listing(
    client: httpx.AsyncClient, headers: dict[str, str], **overrides: Any
) -> dict[str, Any]:
    response = await client.post("/listings", json=listing_body(**overrides), headers=headers)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


async def create_live_listing(
    client: httpx.AsyncClient, headers: dict[str, str], **overrides: Any
) -> dict[str, Any]:
    listing = await create_listing(client, headers, **overrides)
    response = await client.post(f"/listings/{listing['id']}/publish", headers=headers)
    assert response.status_code == 200, response.text
    published: dict[str, Any] = response.json()
    return published


async def feed_auction_update(listing_id: str, *, price: str, status: str = "live") -> FeedResult:
    """Stand in for bidding: apply an auction.updated event the way the feed consumer does."""
    raw = json.dumps(
        {
            "type": "auction.updated",
            "schema_version": 1,
            "listing_id": listing_id,
            "version": 1,
            "status": status,
            "current_price": price,
            "ends_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
        }
    ).encode()
    async with SessionLocal() as session, session.begin():
        return await apply_auction_update(session, raw)


async def settle(
    listing_id: str,
    *,
    status: str,
    winner: uuid.UUID | None = None,
    price: str | None = None,
) -> OutcomeResult:
    """Stand in for bidding: apply an auction.settled event the way the outcome consumer does."""
    raw = json.dumps(
        {
            "type": "auction.settled",
            "schema_version": 1,
            "listing_id": listing_id,
            "version": 3,
            "status": status,
            "winner_id": None if winner is None else str(winner),
            "final_price": price,
        }
    ).encode()
    async with SessionLocal() as session, session.begin():
        return await record_outcome(session, raw)
