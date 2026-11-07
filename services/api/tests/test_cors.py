"""CORS: the frontend may call this service from a browser; other websites may not.

A browser asks first ("preflight", an OPTIONS request) before a cross-origin call that
carries a token, and only goes ahead if the answer names its origin. These tests send
that question the way a browser would.
"""

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings

FRONTEND = "http://localhost:5173"
STRANGER = "https://evil.example"


async def preflight(
    client: httpx.AsyncClient,
    origin: str,
    *,
    method: str = "POST",
    headers: str = "authorization,content-type",
) -> httpx.Response:
    return await client.options(
        "/listings",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": headers,
        },
    )


async def test_the_frontend_may_send_an_authenticated_post(client: httpx.AsyncClient) -> None:
    response = await preflight(client, FRONTEND)

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == FRONTEND
    allowed = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed
    assert "content-type" in allowed
    # Tokens are sent in a header, never in cookies, so the browser is not told to send cookies.
    assert "access-control-allow-credentials" not in response.headers


async def test_another_website_is_refused(client: httpx.AsyncClient) -> None:
    response = await preflight(client, STRANGER)

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


async def test_a_plain_request_names_only_an_allowed_origin(client: httpx.AsyncClient) -> None:
    allowed = await client.get("/healthz", headers={"Origin": FRONTEND})
    assert allowed.headers["access-control-allow-origin"] == FRONTEND

    # The server still answers, but without the header the browser hides the response.
    refused = await client.get("/healthz", headers={"Origin": STRANGER})
    assert refused.status_code == 200
    assert "access-control-allow-origin" not in refused.headers


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
