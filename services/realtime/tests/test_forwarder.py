import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from auction_contracts.events import (
    AuctionSettled,
    AuctionStatus,
    AuctionUpdated,
    ListingPublished,
)

from app.core.channels import auction_channel
from app.core.redis import redis_client
from app.workers.forwarder import Forwarder, forward_until_redis_accepts
from tests.fakes import auction_state, update_bytes
from tests.redis_helpers import next_message, subscribe


async def test_an_update_reaches_its_auction_channel_unchanged() -> None:
    listing_id = uuid.uuid4()
    sent = auction_state(listing_id, version=1, price="125.00")

    pubsub = await subscribe(auction_channel(listing_id))
    try:
        assert await Forwarder().handle(sent.model_dump_json().encode()) is True
        message = await next_message(pubsub)
    finally:
        await pubsub.aclose()

    assert message is not None
    assert AuctionUpdated.model_validate_json(message) == sent


async def test_an_older_or_repeated_version_is_not_forwarded_again() -> None:
    listing_id = uuid.uuid4()
    forwarder = Forwarder()

    pubsub = await subscribe(auction_channel(listing_id))
    try:
        assert await forwarder.handle(update_bytes(listing_id, version=2, price="110.00")) is True
        assert await forwarder.handle(update_bytes(listing_id, version=1, price="105.00")) is False
        assert await forwarder.handle(update_bytes(listing_id, version=2, price="110.00")) is False
        assert await forwarder.handle(update_bytes(listing_id, version=3, price="115.00")) is True

        first = await next_message(pubsub)
        second = await next_message(pubsub)
        assert first is not None and json.loads(first)["version"] == 2
        assert second is not None and json.loads(second)["version"] == 3
        assert await next_message(pubsub, wait_seconds=0.3) is None
    finally:
        await pubsub.aclose()


async def test_a_malformed_message_is_skipped_not_retried() -> None:
    forwarder = Forwarder()
    assert await forwarder.handle(b"not json") is False
    assert await forwarder.handle(b"") is False
    assert await forwarder.handle(b'{"version": 1}') is False  # no type, no listing


async def test_private_events_are_never_forwarded() -> None:
    """Realtime talks to browsers. A winner or a reserve must not get through, even if
    one is put on this topic by mistake."""
    listing_id = uuid.uuid4()
    settled = AuctionSettled(
        listing_id=listing_id,
        version=3,
        status=AuctionStatus.SOLD,
        winner_id=uuid.uuid4(),
        final_price=Decimal("150.00"),
    )
    published = ListingPublished(
        listing_id=listing_id,
        version=0,
        seller_id=uuid.uuid4(),
        start_price=Decimal("100.00"),
        bid_increment=Decimal("5.00"),
        reserve_price=Decimal("500.00"),
        ends_at=datetime.now(UTC) + timedelta(hours=1),
    )

    pubsub = await subscribe(auction_channel(listing_id))
    try:
        forwarder = Forwarder()
        assert await forwarder.handle(settled.model_dump_json().encode()) is False
        assert await forwarder.handle(published.model_dump_json().encode()) is False
        assert await next_message(pubsub, wait_seconds=0.3) is None
    finally:
        await pubsub.aclose()


async def test_a_forgotten_auction_is_forwarded_again_rather_than_lost() -> None:
    a, b, c = (uuid.uuid4() for _ in range(3))
    forwarder = Forwarder(max_auctions=2)

    assert await forwarder.handle(update_bytes(a, version=5, price="150.00")) is True
    assert await forwarder.handle(update_bytes(b, version=1, price="105.00")) is True
    assert await forwarder.handle(update_bytes(c, version=1, price="105.00")) is True
    # The map is full, so a was forgotten: its older update is forwarded, not dropped.
    assert await forwarder.handle(update_bytes(a, version=4, price="145.00")) is True
    assert await forwarder.handle(update_bytes(c, version=1, price="105.00")) is False  # remembered


async def test_a_redis_outage_delays_an_update_but_never_loses_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing_id = uuid.uuid4()
    pubsub = await subscribe(auction_channel(listing_id))
    real_publish = redis_client.publish
    outages_left = 2

    async def flaky_publish(channel: str, message: str) -> int:
        nonlocal outages_left
        if outages_left > 0:
            outages_left -= 1
            raise ConnectionError("redis down")
        return await real_publish(channel, message)

    monkeypatch.setattr(redis_client, "publish", flaky_publish)
    try:
        # Retries through the outage, then delivers.
        await forward_until_redis_accepts(
            Forwarder(), update_bytes(listing_id, version=1, price="125.00")
        )
        message = await next_message(pubsub)
    finally:
        await pubsub.aclose()

    assert outages_left == 0
    assert message is not None
    assert json.loads(message)["version"] == 1
