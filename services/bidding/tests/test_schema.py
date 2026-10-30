import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from app.core.db import Base, SessionLocal, engine
from app.models import Auction, Bid


def make_auction(
    *,
    status: str = "live",
    start_price: str = "100.00",
    current_price: str = "100.00",
    reserve_price: str | None = None,
) -> Auction:
    return Auction(
        listing_id=uuid.uuid4(),
        seller_id=uuid.uuid4(),
        status=status,
        start_price=Decimal(start_price),
        bid_increment=Decimal("5.00"),
        reserve_price=None if reserve_price is None else Decimal(reserve_price),
        current_price=Decimal(current_price),
        ends_at=datetime.now(UTC) + timedelta(hours=1),
        version=1,
    )


def make_bid(
    listing_id: uuid.UUID,
    *,
    bidder_id: uuid.UUID | None = None,
    max_amount: str = "150.00",
    key: str = "key-1",
) -> Bid:
    return Bid(
        listing_id=listing_id,
        bidder_id=bidder_id or uuid.uuid4(),
        max_amount=Decimal(max_amount),
        idempotency_key=key,
    )


async def save(*rows: Auction | Bid) -> None:
    async with SessionLocal() as session:
        session.add_all(rows)
        await session.commit()


async def test_valid_auction_and_bid_are_stored() -> None:
    auction = make_auction()
    await save(auction)
    bid = make_bid(auction.listing_id)
    await save(bid)

    async with SessionLocal() as session:
        stored = await session.get(Bid, bid.id)

    assert stored is not None
    assert stored.max_amount == Decimal("150.00")


async def test_bid_must_reference_a_known_auction() -> None:
    with pytest.raises(IntegrityError):
        await save(make_bid(uuid.uuid4()))


async def test_bid_must_be_positive() -> None:
    auction = make_auction()
    await save(auction)
    with pytest.raises(IntegrityError):
        await save(make_bid(auction.listing_id, max_amount="0.00"))


async def test_current_price_cannot_drop_below_start() -> None:
    with pytest.raises(IntegrityError):
        await save(make_auction(start_price="100.00", current_price="99.99"))


async def test_reserve_cannot_be_below_start() -> None:
    with pytest.raises(IntegrityError):
        await save(make_auction(start_price="100.00", reserve_price="90.00"))


async def test_idempotency_key_is_unique_per_bidder() -> None:
    auction = make_auction()
    await save(auction)
    bidder = uuid.uuid4()
    await save(make_bid(auction.listing_id, bidder_id=bidder, key="same"))

    with pytest.raises(IntegrityError):
        await save(make_bid(auction.listing_id, bidder_id=bidder, key="same"))

    # A different bidder may reuse the same key.
    await save(make_bid(auction.listing_id, bidder_id=uuid.uuid4(), key="same"))


async def test_unknown_status_is_rejected() -> None:
    with pytest.raises(IntegrityError):
        await save(make_auction(status="pending"))


async def test_models_and_migrations_agree() -> None:
    """Fails when a model changes without a matching migration."""
    async with engine.connect() as conn:
        differences = await conn.run_sync(_compare_with_models)
    assert differences == []


def _compare_with_models(sync_conn: Connection) -> list[object]:
    return list(compare_metadata(MigrationContext.configure(sync_conn), Base.metadata))