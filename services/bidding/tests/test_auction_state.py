import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.core.db import SessionLocal
from app.models import Auction
from app.services.auction_state import ApplyOutcome
from app.workers.auction_sync import process_message


def published(
    listing_id: uuid.UUID,
    *,
    version: int = 0,
    start: str = "100.00",
    reserve: str | None = None,
) -> bytes:
    message = {
        "type": "listing.published",
        "schema_version": 1,
        "listing_id": str(listing_id),
        "version": version,
        "seller_id": str(uuid.uuid4()),
        "start_price": start,
        "bid_increment": "5.00",
        "reserve_price": reserve,
        "ends_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }
    return json.dumps(message).encode()


async def apply(raw: bytes) -> ApplyOutcome:
    return await process_message(SessionLocal, raw)


async def stored(listing_id: uuid.UUID) -> Auction | None:
    async with SessionLocal() as session:
        return await session.get(Auction, listing_id)


async def test_a_publish_creates_a_live_auction() -> None:
    listing_id = uuid.uuid4()
    outcome = await apply(published(listing_id, reserve="150.00"))

    assert outcome is ApplyOutcome.APPLIED
    auction = await stored(listing_id)
    assert auction is not None
    assert auction.status == "live"
    assert auction.current_price == Decimal("100.00")
    assert auction.reserve_price == Decimal("150.00")
    assert auction.version == 0


async def test_a_replayed_publish_changes_nothing() -> None:
    listing_id = uuid.uuid4()
    raw = published(listing_id)
    assert await apply(raw) is ApplyOutcome.APPLIED
    assert await apply(raw) is ApplyOutcome.STALE


async def test_an_older_version_never_overwrites_a_newer_one() -> None:
    listing_id = uuid.uuid4()
    assert await apply(published(listing_id, version=3, start="100.00")) is ApplyOutcome.APPLIED
    assert await apply(published(listing_id, version=2, start="80.00")) is ApplyOutcome.STALE

    auction = await stored(listing_id)
    assert auction is not None
    assert auction.start_price == Decimal("100.00")
    assert auction.version == 3


async def test_a_malformed_message_is_skipped() -> None:
    assert await apply(b"not json at all") is ApplyOutcome.MALFORMED

    async with SessionLocal() as session:
        count = len(list(await session.scalars(Auction.__table__.select())))
    assert count == 0


async def test_an_unusable_publish_is_skipped_not_retried() -> None:
    """A message the database would refuse must not be retried forever."""
    listing_id = uuid.uuid4()
    assert await apply(published(listing_id, start="0.00")) is ApplyOutcome.MALFORMED
    assert await stored(listing_id) is None


async def test_other_event_types_are_ignored() -> None:
    raw = json.dumps(
        {
            "type": "auction.updated",
            "schema_version": 1,
            "listing_id": str(uuid.uuid4()),
            "version": 1,
            "status": "live",
            "current_price": "100.00",
            "ends_at": "2026-11-01T12:00:00Z",
        }
    ).encode()
    assert await apply(raw) is ApplyOutcome.IGNORED
