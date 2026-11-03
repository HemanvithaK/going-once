import json
import uuid

import httpx
import pytest

from app.core.bidding_client import BiddingClient, BiddingUnavailableError
from tests.fakes import FakeBidding, auction_state, free_port


def client_for(url: str) -> BiddingClient:
    return BiddingClient(httpx.AsyncClient(base_url=url, timeout=2.0))


async def test_a_known_auction_comes_back_as_a_checked_state(bidding: FakeBidding) -> None:
    listing_id = uuid.uuid4()
    bidding.put(auction_state(listing_id, version=4, price="125.00"))
    client = client_for(bidding.url)
    try:
        state = await client.fetch_state(listing_id)
    finally:
        await client.aclose()

    assert state is not None
    assert state.version == 4
    assert str(state.current_price) == "125.00"


async def test_an_unknown_auction_is_none(bidding: FakeBidding) -> None:
    client = client_for(bidding.url)
    try:
        assert await client.fetch_state(uuid.uuid4()) is None
    finally:
        await client.aclose()


@pytest.mark.parametrize("status", [500, 502, 503])
async def test_a_server_error_means_unavailable(bidding: FakeBidding, status: int) -> None:
    bidding.fail_status = status
    client = client_for(bidding.url)
    try:
        with pytest.raises(BiddingUnavailableError):
            await client.fetch_state(uuid.uuid4())
    finally:
        await client.aclose()


async def test_a_refused_connection_means_unavailable() -> None:
    client = client_for(f"http://127.0.0.1:{free_port()}")  # nothing listens there
    try:
        with pytest.raises(BiddingUnavailableError):
            await client.fetch_state(uuid.uuid4())
    finally:
        await client.aclose()


async def test_a_slow_bidding_means_unavailable(bidding: FakeBidding) -> None:
    bidding.delay_seconds = 1.0
    client = BiddingClient(httpx.AsyncClient(base_url=bidding.url, timeout=0.2))
    try:
        with pytest.raises(BiddingUnavailableError):
            await client.fetch_state(uuid.uuid4())
    finally:
        await client.aclose()


async def test_a_body_that_is_not_json_means_unavailable(bidding: FakeBidding) -> None:
    bidding.raw_body = "<html>oops</html>"
    client = client_for(bidding.url)
    try:
        with pytest.raises(BiddingUnavailableError):
            await client.fetch_state(uuid.uuid4())
    finally:
        await client.aclose()


async def test_a_state_with_a_field_viewers_must_not_see_is_refused(
    bidding: FakeBidding,
) -> None:
    """Realtime checks the answer against the contract. A leaked reserve is an error,
    not something to pass on to a browser."""
    listing_id = uuid.uuid4()
    state = auction_state(listing_id, version=1, price="125.00")
    leaky = state.model_dump(mode="json") | {"reserve_price": "500.00"}
    bidding.raw_body = json.dumps(leaky)
    client = client_for(bidding.url)
    try:
        with pytest.raises(BiddingUnavailableError):
            await client.fetch_state(listing_id)
    finally:
        await client.aclose()
