"""Setup shared by the bidding tests: open auctions the way the real pipeline does."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import jwt
from sqlalchemy import update

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.security import JWT_ALGORITHM
from app.models import Auction
from app.services.auction_state import ApplyOutcome
from app.workers.auction_sync import process_message


def publish_bytes(
    listing_id: uuid.UUID,
    seller_id: uuid.UUID,
    *,
    start: str,
    increment: str,
    ends_in: timedelta,
    reserve: str | None,
) -> bytes:
    message = {
        "type": "listing.published",
        "schema_version": 1,
        "listing_id": str(listing_id),
        "version": 0,
        "seller_id": str(seller_id),
        "start_price": start,
        "bid_increment": increment,
        "reserve_price": reserve,
        "ends_at": (datetime.now(UTC) + ends_in).isoformat(),
    }
    return json.dumps(message).encode()


async def open_auction(
    *,
    seller_id: uuid.UUID | None = None,
    start: str = "100.00",
    increment: str = "5.00",
    ends_in: timedelta = timedelta(hours=1),
    reserve: str | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Make a live auction through listing-events. Returns (listing_id, seller_id)."""
    listing_id = uuid.uuid4()
    seller = seller_id or uuid.uuid4()
    raw = publish_bytes(
        listing_id,
        seller,
        start=start,
        increment=increment,
        ends_in=ends_in,
        reserve=reserve,
    )
    outcome = await process_message(SessionLocal, raw)
    assert outcome is ApplyOutcome.APPLIED
    return listing_id, seller


async def make_due(listing_id: uuid.UUID) -> None:
    """Move an auction's deadline into the past, so the closer will pick it up."""
    async with SessionLocal() as session, session.begin():
        await session.execute(
            update(Auction)
            .where(Auction.listing_id == listing_id)
            .values(ends_at=datetime.now(UTC) - timedelta(seconds=1))
        )


async def place(
    client: httpx.AsyncClient,
    listing_id: uuid.UUID,
    bidder: uuid.UUID,
    amount: str,
    key: str,
) -> httpx.Response:
    return await client.post(
        f"/listings/{listing_id}/bids",
        json={"max_amount": amount},
        headers={**auth_headers(bidder), "Idempotency-Key": key},
    )


def auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    """A signed access token for this user, in the same shape the core API issues."""
    now = datetime.now(UTC)
    token = jwt.encode(
        {"sub": str(user_id), "typ": "access", "iat": now, "exp": now + timedelta(minutes=15)},
        get_settings().jwt_secret,
        algorithm=JWT_ALGORITHM,
    )
    return {"Authorization": f"Bearer {token}"}
