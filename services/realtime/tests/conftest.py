import os

# The settings have no defaults for these, so set them before the app is imported.
# A real shell can override either one, for example to point at a different Redis.
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BIDDING_URL", "http://bidding.invalid")

from collections.abc import AsyncIterator

import httpx
import pytest

from app.core.bidding_client import BiddingClient, get_bidding_client
from app.main import app
from tests.fakes import FakeBidding, serve


@pytest.fixture
async def bidding() -> AsyncIterator[FakeBidding]:
    """A running fake bidding service. Tests put auction states into it."""
    fake = FakeBidding()
    async with serve(fake.app) as port:
        fake.url = f"http://127.0.0.1:{port}"
        yield fake


@pytest.fixture
async def realtime_url(bidding: FakeBidding) -> AsyncIterator[str]:
    """The real realtime app on a local port, asking the fake bidding for snapshots."""
    client = BiddingClient(httpx.AsyncClient(base_url=bidding.url, timeout=3.0))
    app.dependency_overrides[get_bidding_client] = lambda: client
    async with serve(app) as port:
        yield f"ws://127.0.0.1:{port}"
    app.dependency_overrides.pop(get_bidding_client, None)
    await client.aclose()
