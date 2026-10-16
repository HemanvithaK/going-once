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


# --- creating -------------------------------------------------------------


async def test_create_returns_a_draft(client: httpx.AsyncClient) -> None:
    headers = await make_user(client, "seller@example.com")
    listing = await create_listing(client, headers)
    assert listing["status"] == "draft"
    assert listing["current_price"] == "100.00"


async def test_create_requires_authentication(client: httpx.AsyncClient) -> None:
    response = await client.post("/listings", json=listing_body())
    assert response.status_code == 401


async def test_end_before_start_is_rejected(client: httpx.AsyncClient) -> None:
    headers = await make_user(client, "seller@example.com")
    now = datetime.now(UTC)
    response = await client.post(
        "/listings",
        json=listing_body(
            starts_at=(now + timedelta(days=1)).isoformat(),
            ends_at=now.isoformat(),
        ),
        headers=headers,
    )
    assert response.status_code == 422


async def test_reserve_below_start_is_rejected(client: httpx.AsyncClient) -> None:
    headers = await make_user(client, "seller@example.com")
    response = await client.post(
        "/listings",
        json=listing_body(start_price="100.00", reserve_price="50.00"),
        headers=headers,
    )
    assert response.status_code == 422


async def test_negative_price_is_rejected(client: httpx.AsyncClient) -> None:
    headers = await make_user(client, "seller@example.com")
    response = await client.post(
        "/listings", json=listing_body(start_price="-5.00"), headers=headers
    )
    assert response.status_code == 422


# --- the reserve price must not leak -------------------------------------


async def test_seller_sees_their_own_reserve(client: httpx.AsyncClient) -> None:
    headers = await make_user(client, "seller@example.com")
    listing = await create_live_listing(client, headers, reserve_price="500.00")
    response = await client.get(f"/listings/{listing['id']}", headers=headers)
    assert response.json()["reserve_price"] == "500.00"
    assert response.json()["has_reserve"] is True


async def test_buyer_never_sees_the_reserve(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    buyer = await make_user(client, "buyer@example.com")
    listing = await create_live_listing(client, seller, reserve_price="500.00")

    response = await client.get(f"/listings/{listing['id']}", headers=buyer)
    assert response.status_code == 200
    assert response.json()["reserve_price"] is None
    # They may know one exists — just not what it is.
    assert response.json()["has_reserve"] is True
    assert "500.00" not in response.text


async def test_anonymous_viewer_never_sees_the_reserve(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    listing = await create_live_listing(client, seller, reserve_price="500.00")
    response = await client.get(f"/listings/{listing['id']}")
    assert response.status_code == 200
    assert "500.00" not in response.text


# --- ownership ------------------------------------------------------------


async def test_a_stranger_cannot_edit_your_listing(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    stranger = await make_user(client, "stranger@example.com")
    listing = await create_listing(client, seller)

    response = await client.patch(
        f"/listings/{listing['id']}", json={"title": "Hijacked"}, headers=stranger
    )
    # 404, not 403: a stranger should not learn the listing exists.
    assert response.status_code == 404


async def test_a_stranger_cannot_publish_your_listing(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    stranger = await make_user(client, "stranger@example.com")
    listing = await create_listing(client, seller)

    response = await client.post(f"/listings/{listing['id']}/publish", headers=stranger)
    assert response.status_code == 404


async def test_a_draft_is_invisible_to_everyone_else(client: httpx.AsyncClient) -> None:
    seller = await make_user(client, "seller@example.com")
    buyer = await make_user(client, "buyer@example.com")
    listing = await create_listing(client, seller)

    assert (await client.get(f"/listings/{listing['id']}", headers=buyer)).status_code == 404
    assert (await client.get(f"/listings/{listing['id']}")).status_code == 404
    assert (await client.get(f"/listings/{listing['id']}", headers=seller)).status_code == 200


# --- editing --------------------------------------------------------------


async def test_patch_changes_only_what_was_sent(client: httpx.AsyncClient) -> None:
    headers = await make_user(client, "seller@example.com")
    listing = await create_listing(client, headers)

    response = await client.patch(
        f"/listings/{listing['id']}", json={"title": "Renamed"}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Renamed"
    assert response.json()["description"] == listing["description"]
    assert response.json()["start_price"] == listing["start_price"]


async def test_a_live_listing_cannot_be_edited(client: httpx.AsyncClient) -> None:
    headers = await make_user(client, "seller@example.com")
    listing = await create_live_listing(client, headers)

    response = await client.patch(
        f"/listings/{listing['id']}", json={"start_price": "1.00"}, headers=headers
    )
    assert response.status_code == 409


# --- publishing -----------------------------------------------------------


async def test_publishing_a_started_listing_makes_it_live(client: httpx.AsyncClient) -> None:
    headers = await make_user(client, "seller@example.com")
    listing = await create_live_listing(client, headers)
    assert listing["status"] == "live"


async def test_publishing_a_future_listing_schedules_it(client: httpx.AsyncClient) -> None:
    headers = await make_user(client, "seller@example.com")
    now = datetime.now(UTC)
    listing = await create_listing(
        client,
        headers,
        starts_at=(now + timedelta(days=1)).isoformat(),
        ends_at=(now + timedelta(days=3)).isoformat(),
    )
    response = await client.post(f"/listings/{listing['id']}/publish", headers=headers)
    assert response.json()["status"] == "scheduled"


async def test_publishing_twice_is_409(client: httpx.AsyncClient) -> None:
    headers = await make_user(client, "seller@example.com")
    listing = await create_live_listing(client, headers)
    response = await client.post(f"/listings/{listing['id']}/publish", headers=headers)
    assert response.status_code == 409


# --- browsing and pagination ---------------------------------------------


async def test_browse_shows_only_live_listings(client: httpx.AsyncClient) -> None:
    headers = await make_user(client, "seller@example.com")
    await create_listing(client, headers, title="Still a draft")
    await create_live_listing(client, headers, title="Open for bids")

    response = await client.get("/listings")
    titles = [item["title"] for item in response.json()["items"]]
    assert titles == ["Open for bids"]


async def test_browse_is_ordered_by_soonest_to_close(client: httpx.AsyncClient) -> None:
    headers = await make_user(client, "seller@example.com")
    now = datetime.now(UTC)
    for days, title in [(5, "last"), (1, "first"), (3, "middle")]:
        await create_live_listing(
            client, headers, title=title, ends_at=(now + timedelta(days=days)).isoformat()
        )

    response = await client.get("/listings")
    titles = [item["title"] for item in response.json()["items"]]
    assert titles == ["first", "middle", "last"]


async def test_pagination_walks_every_listing_exactly_once(
    client: httpx.AsyncClient,
) -> None:
    headers = await make_user(client, "seller@example.com")
    now = datetime.now(UTC)
    for i in range(7):
        await create_live_listing(
            client,
            headers,
            title=f"item-{i}",
            ends_at=(now + timedelta(days=i + 1)).isoformat(),
        )

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):  # generous bound, so a broken cursor can't loop forever
        url = "/listings?limit=3" + (f"&cursor={cursor}" if cursor else "")
        page = (await client.get(url)).json()
        seen.extend(item["title"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert seen == [f"item-{i}" for i in range(7)]
    assert len(seen) == len(set(seen))


async def test_last_page_has_no_cursor(client: httpx.AsyncClient) -> None:
    headers = await make_user(client, "seller@example.com")
    await create_live_listing(client, headers)
    page = (await client.get("/listings?limit=20")).json()
    assert page["next_cursor"] is None


async def test_a_malformed_cursor_is_422(client: httpx.AsyncClient) -> None:
    response = await client.get("/listings?cursor=not-a-real-cursor")
    assert response.status_code == 422


async def test_mine_includes_drafts_and_excludes_other_sellers(
    client: httpx.AsyncClient,
) -> None:
    seller = await make_user(client, "seller@example.com")
    other = await make_user(client, "other@example.com")
    await create_listing(client, seller, title="my draft")
    await create_live_listing(client, other, title="their listing")

    titles = [item["title"] for item in (await client.get("/listings/mine", headers=seller)).json()]
    assert titles == ["my draft"]

# --- timezones ------------------------------------------------------------


def naive_time(days: float) -> str:
    """An ISO time with no offset, like '2026-10-20T10:00:00'. These must be rejected."""
    return (datetime.now(UTC) + timedelta(days=days)).replace(tzinfo=None).isoformat()


async def test_create_rejects_a_time_with_no_timezone(client: httpx.AsyncClient) -> None:
    headers = await make_user(client, "seller@example.com")
    response = await client.post(
        "/listings",
        json=listing_body(starts_at=naive_time(-0.1), ends_at=naive_time(2)),
        headers=headers,
    )
    assert response.status_code == 422


async def test_patch_cannot_clear_a_required_field(client: httpx.AsyncClient) -> None:
    headers = await make_user(client, "seller@example.com")
    listing = await create_listing(client, headers)
    response = await client.patch(
        f"/listings/{listing['id']}", json={"title": None}, headers=headers
    )
    assert response.status_code == 422


async def test_patch_rejects_a_time_with_no_timezone(client: httpx.AsyncClient) -> None:
    headers = await make_user(client, "seller@example.com")
    listing = await create_listing(client, headers)
    response = await client.patch(
        f"/listings/{listing['id']}",
        json={"starts_at": naive_time(-0.1)},
        headers=headers,
    )
    assert response.status_code == 422