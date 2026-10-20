from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

PASSWORD = "a-long-enough-password"


async def make_user(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    """Register and log in, returning an Authorization header."""
    await client.post(
        "/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": email.split("@")[0]},
    )
    response = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def listing_body(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    body: dict[str, Any] = {
        "title": "Vintage camera",
        "description": "A nice one.",
        "start_price": "100.00",
        "bid_increment": "5.00",
        "starts_at": (now - timedelta(minutes=1)).isoformat(),
        "ends_at": (now + timedelta(days=2)).isoformat(),
    }
    body.update(overrides)
    return body


async def create_listing(
    client: httpx.AsyncClient, headers: dict[str, str], **overrides: Any
) -> dict[str, Any]:
    response = await client.post("/listings", json=listing_body(**overrides), headers=headers)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


async def create_live_listing(
    client: httpx.AsyncClient, headers: dict[str, str], **overrides: Any
) -> dict[str, Any]:
    listing = await create_listing(client, headers, **overrides)
    response = await client.post(f"/listings/{listing['id']}/publish", headers=headers)
    assert response.status_code == 200, response.text
    published: dict[str, Any] = response.json()
    return published