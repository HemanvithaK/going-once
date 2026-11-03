"""Asks bidding for an auction's current public state.

Realtime has no database, so a viewer's first snapshot comes from the service that owns
the state. The answer is checked against the contract before it goes anywhere near a
browser: if bidding ever sent a field a viewer must not see, the check fails and the
viewer gets an error instead of the field.
"""

import uuid

import httpx
from auction_contracts.events import AuctionUpdated
from pydantic import ValidationError

from app.core.config import get_settings


class BiddingUnavailableError(Exception):
    """Bidding could not give a usable answer. The viewer may try again later."""


class BiddingClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def fetch_state(self, listing_id: uuid.UUID) -> AuctionUpdated | None:
        """The auction's public state, or None if bidding does not know the auction."""
        try:
            response = await self._http.get(f"/listings/{listing_id}/state")
        except httpx.HTTPError as exc:
            raise BiddingUnavailableError(str(exc) or type(exc).__name__) from exc

        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise BiddingUnavailableError(f"bidding answered {response.status_code}")
        try:
            return AuctionUpdated.model_validate_json(response.content)
        except ValidationError as exc:
            raise BiddingUnavailableError("bidding sent a state that breaks the contract") from exc

    async def aclose(self) -> None:
        await self._http.aclose()


_client: BiddingClient | None = None


def get_bidding_client() -> BiddingClient:
    """The process's one client, created on first use. Tests replace it with an override."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = BiddingClient(
            httpx.AsyncClient(
                base_url=settings.bidding_url,
                timeout=settings.bidding_timeout_seconds,
            )
        )
    return _client


async def close_bidding_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
