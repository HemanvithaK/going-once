"""CORS: the frontend may place bids from a browser; other websites may not.

A browser asks first ("preflight", an OPTIONS request) before a cross-origin call that
carries a token, and only goes ahead if the answer names its origin and every header the
call will send. A bid sends Idempotency-Key, so that header must be allowed too.
"""

import uuid

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings

FRONTEND = "http://localhost:5173"
STRANGER = "https://evil.example"


async def preflight_bid(
    client: httpx.AsyncClient,
    origin: str,
    *,
    method: str = "POST",
    headers: str = "authorization,content-type,idempotency-key",
) -> httpx.Response:
    return await client.options(
        f"/listings/{uuid.uuid4()}/bids",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": headers,
        },
    )


async def test_the_frontend_may_place_a_bid(client: httpx.AsyncClient) -> None:
    response = await preflight_bid(client, FRONTEND)

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == FRONTEND
    allowed = response.headers["access-control-allow-headers"].lower()
    assert "idempotency-key" in allowed  # without it, the browser blocks every bid
    assert "authorization" in allowed
    assert "access-control-allow-credentials" not in response.headers


async def test_another_website_cannot_place_a_bid(client: httpx.AsyncClient) -> None:
    response = await preflight_bid(client, STRANGER)

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


async def test_a_method_bidding_does_not_offer_is_refused(client: httpx.AsyncClient) -> None:
    response = await preflight_bid(client, FRONTEND, method="DELETE")

    assert response.status_code == 400


async def test_the_frontend_may_read_an_auctions_state(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/listings/{uuid.uuid4()}/state", headers={"Origin": FRONTEND})

    # Unknown auction, so 404, but the browser is still allowed to read the answer.
    assert response.headers["access-control-allow-origin"] == FRONTEND


def test_origins_are_read_from_a_comma_separated_list() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://unused",
        jwt_secret="x" * 32,
        cors_origins=" https://a.example/ , ,https://b.example",
        _env_file=None,  # type: ignore[call-arg]
    )
    assert settings.cors_origin_list == ["https://a.example", "https://b.example"]


def test_a_wildcard_origin_is_refused_at_startup() -> None:
    with pytest.raises(ValidationError, match="exact origins"):
        Settings(
            database_url="postgresql+asyncpg://unused",
            jwt_secret="x" * 32,
            cors_origins="https://a.example, *",
            _env_file=None,  # type: ignore[call-arg]
        )
