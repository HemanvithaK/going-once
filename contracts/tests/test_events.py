import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from auction_contracts.events import (
    AuctionSettled,
    AuctionStatus,
    AuctionUpdated,
    ListingPublished,
    parse_event,
)

LISTING_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
SELLER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
WINNER_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
ENDS_AT = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def auction_update_json(**overrides: object) -> str:
    message: dict[str, object] = {
        "type": "auction.updated",
        "schema_version": 1,
        "listing_id": str(LISTING_ID),
        "version": 3,
        "status": "live",
        "current_price": "125.00",
        "ends_at": "2026-11-01T12:00:00Z",
    }
    message.update(overrides)
    return json.dumps(message)


def test_a_published_listing_round_trips_through_json() -> None:
    event = ListingPublished(
        listing_id=LISTING_ID,
        version=1,
        seller_id=SELLER_ID,
        start_price=Decimal("100.00"),
        bid_increment=Decimal("5.00"),
        reserve_price=None,
        ends_at=ENDS_AT,
    )
    assert parse_event(event.model_dump_json()) == event


def test_an_auction_update_keeps_the_cents_on_the_wire() -> None:
    event = AuctionUpdated(
        listing_id=LISTING_ID,
        version=3,
        status=AuctionStatus.LIVE,
        current_price=Decimal("125.00"),
        ends_at=ENDS_AT,
    )
    raw = event.model_dump_json()
    assert '"current_price":"125.00"' in raw
    assert parse_event(raw) == event


def test_the_public_update_has_no_room_for_a_reserve() -> None:
    assert "reserve_price" not in AuctionUpdated.model_fields
    with pytest.raises(ValidationError):
        parse_event(auction_update_json(reserve_price="500.00"))


def test_a_sold_auction_needs_a_winner_and_a_price() -> None:
    with pytest.raises(ValidationError):
        AuctionSettled(listing_id=LISTING_ID, version=4, status=AuctionStatus.SOLD)

    sold = AuctionSettled(
        listing_id=LISTING_ID,
        version=4,
        status=AuctionStatus.SOLD,
        winner_id=WINNER_ID,
        final_price=Decimal("150.00"),
    )
    assert parse_event(sold.model_dump_json()) == sold


def test_an_unsold_auction_names_no_winner() -> None:
    with pytest.raises(ValidationError):
        AuctionSettled(
            listing_id=LISTING_ID,
            version=4,
            status=AuctionStatus.UNSOLD,
            winner_id=WINNER_ID,
        )


def test_an_unknown_event_type_is_rejected() -> None:
    raw = json.dumps(
        {"type": "bid.placed", "schema_version": 1, "listing_id": str(LISTING_ID), "version": 1}
    )
    with pytest.raises(ValidationError):
        parse_event(raw)


def test_a_newer_schema_version_is_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_event(auction_update_json(schema_version=2))


def test_a_publish_is_version_zero() -> None:
    """The listing as first published is version 0. Bids start at version 1."""
    published = ListingPublished(
        listing_id=LISTING_ID,
        version=0,
        seller_id=SELLER_ID,
        start_price=Decimal("100.00"),
        bid_increment=Decimal("5.00"),
        reserve_price=None,
        ends_at=ENDS_AT,
    )
    assert parse_event(published.model_dump_json()) == published


def test_a_negative_version_is_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_event(auction_update_json(version=-1))


def test_an_extra_field_is_rejected_not_silently_dropped() -> None:
    with pytest.raises(ValidationError):
        parse_event(auction_update_json(debug=True))