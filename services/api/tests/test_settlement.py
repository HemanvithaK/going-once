import json
import uuid
from decimal import Decimal

import httpx
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.core.events import LISTING_SNAPSHOTS_TOPIC
from app.models.listing import Listing, ListingStatus
from app.models.order import Order
from app.models.outbox import OutboxEvent
from app.models.user import User
from app.services.settlement import OutcomeResult, record_outcome
from tests.helpers import create_live_listing, make_user


def settled(
    listing_id: uuid.UUID,
    *,
    status: str,
    winner: uuid.UUID | None = None,
    price: str | None = None,
) -> bytes:
    return json.dumps(
        {
            "type": "auction.settled",
            "schema_version": 1,
            "listing_id": str(listing_id),
            "version": 3,
            "status": status,
            "winner_id": None if winner is None else str(winner),
            "final_price": price,
        }
    ).encode()


async def apply(raw: bytes) -> OutcomeResult:
    async with SessionLocal() as session, session.begin():
        return await record_outcome(session, raw)


async def user_id(email: str) -> uuid.UUID:
    async with SessionLocal() as session:
        found = await session.scalar(select(User.id).where(User.email == email))
    assert found is not None
    return found


async def read_listing(listing_id: uuid.UUID) -> Listing:
    async with SessionLocal() as session:
        listing = await session.get(Listing, listing_id)
    assert listing is not None
    return listing


async def count_orders() -> int:
    async with SessionLocal() as session:
        return await session.scalar(select(func.count()).select_from(Order)) or 0


async def count_snapshots() -> int:
    async with SessionLocal() as session:
        return (
            await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.topic == LISTING_SNAPSHOTS_TOPIC)
            )
            or 0
        )


async def live_listing(client: httpx.AsyncClient) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Returns (listing_id, seller_id, buyer_id) for a listing that is LIVE."""
    seller_headers = await make_user(client, "seller@example.com")
    await make_user(client, "buyer@example.com")
    listing = await create_live_listing(client, seller_headers)
    listing_id = uuid.UUID(listing["id"])
    return listing_id, await user_id("seller@example.com"), await user_id("buyer@example.com")


async def test_a_sold_outcome_marks_the_listing_sold_and_writes_one_order(
    client: httpx.AsyncClient,
) -> None:
    listing_id, seller, buyer = await live_listing(client)

    result = await apply(settled(listing_id, status="sold", winner=buyer, price="125.00"))

    assert result is OutcomeResult.RECORDED
    listing = await read_listing(listing_id)
    assert listing.status is ListingStatus.SOLD
    assert listing.winner_id == buyer
    assert listing.current_price == Decimal("125.00")
    async with SessionLocal() as session:
        [order] = list(await session.scalars(select(Order)))
    assert order.listing_id == listing_id
    assert order.buyer_id == buyer
    assert order.seller_id == seller
    assert order.amount == Decimal("125.00")


async def test_an_unsold_outcome_marks_the_listing_unsold_with_no_order(
    client: httpx.AsyncClient,
) -> None:
    listing_id, _, _ = await live_listing(client)

    result = await apply(settled(listing_id, status="unsold"))

    assert result is OutcomeResult.RECORDED
    assert (await read_listing(listing_id)).status is ListingStatus.UNSOLD
    assert await count_orders() == 0


async def test_a_repeated_outcome_is_recorded_once(client: httpx.AsyncClient) -> None:
    listing_id, _, buyer = await live_listing(client)
    raw = settled(listing_id, status="sold", winner=buyer, price="125.00")

    assert await apply(raw) is OutcomeResult.RECORDED
    assert await apply(raw) is OutcomeResult.ALREADY_SETTLED
    assert await count_orders() == 1


async def test_an_outcome_for_an_unknown_listing_changes_nothing() -> None:
    result = await apply(settled(uuid.uuid4(), status="unsold"))

    assert result is OutcomeResult.UNKNOWN_LISTING
    assert await count_orders() == 0


async def test_a_malformed_outcome_is_skipped() -> None:
    assert await apply(b"not json at all") is OutcomeResult.MALFORMED


async def test_a_recorded_outcome_queues_a_snapshot_for_viewers(
    client: httpx.AsyncClient,
) -> None:
    listing_id, _, _ = await live_listing(client)
    before = await count_snapshots()

    assert await apply(settled(listing_id, status="unsold")) is OutcomeResult.RECORDED

    assert await count_snapshots() == before + 1
