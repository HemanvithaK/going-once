import httpx

GOOD_PASSWORD = "a-long-enough-password"


async def register(client: httpx.AsyncClient, email: str = "bidder@example.com") -> httpx.Response:
    return await client.post(
        "/auth/register",
        json={"email": email, "password": GOOD_PASSWORD, "display_name": "Bidder"},
    )


async def login(client: httpx.AsyncClient, email: str = "bidder@example.com") -> dict[str, str]:
    response = await client.post("/auth/login", json={"email": email, "password": GOOD_PASSWORD})
    assert response.status_code == 200
    tokens: dict[str, str] = response.json()
    return tokens


# --- register -------------------------------------------------------------


async def test_register_creates_a_user(client: httpx.AsyncClient) -> None:
    response = await register(client)
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "bidder@example.com"
    assert body["role"] == "user"


async def test_register_never_returns_the_password(client: httpx.AsyncClient) -> None:
    response = await register(client)
    assert response.status_code == 201
    assert GOOD_PASSWORD not in response.text
    assert "password" not in response.text


async def test_duplicate_email_is_409_not_500(client: httpx.AsyncClient) -> None:
    await register(client)
    second = await register(client)
    assert second.status_code == 409


async def test_email_is_stored_lowercased(client: httpx.AsyncClient) -> None:
    await register(client, email="MixedCase@Example.com")
    response = await client.post(
        "/auth/login", json={"email": "mixedcase@example.com", "password": GOOD_PASSWORD}
    )
    assert response.status_code == 200


async def test_short_password_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/auth/register",
        json={"email": "x@example.com", "password": "short", "display_name": "X"},
    )
    assert response.status_code == 422


async def test_malformed_email_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/auth/register",
        json={"email": "not-an-email", "password": GOOD_PASSWORD, "display_name": "X"},
    )
    assert response.status_code == 422


# --- login ----------------------------------------------------------------


async def test_login_returns_a_token_pair(client: httpx.AsyncClient) -> None:
    await register(client)
    tokens = await login(client)
    assert tokens["token_type"] == "bearer"
    assert tokens["access_token"] and tokens["refresh_token"]
    assert tokens["expires_in"] == 15 * 60


async def test_wrong_password_is_401(client: httpx.AsyncClient) -> None:
    await register(client)
    response = await client.post(
        "/auth/login", json={"email": "bidder@example.com", "password": "wrong-password-x"}
    )
    assert response.status_code == 401


async def test_unknown_email_is_401_with_the_same_message(client: httpx.AsyncClient) -> None:
    """Must not reveal whether an account exists."""
    await register(client)
    unknown = await client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": GOOD_PASSWORD}
    )
    wrong_pw = await client.post(
        "/auth/login", json={"email": "bidder@example.com", "password": "wrong-password-x"}
    )
    assert unknown.status_code == wrong_pw.status_code == 401
    assert unknown.json() == wrong_pw.json()


# --- authenticated requests ----------------------------------------------


async def test_me_returns_the_caller(client: httpx.AsyncClient) -> None:
    await register(client)
    tokens = await login(client)
    response = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert response.status_code == 200
    assert response.json()["email"] == "bidder@example.com"


async def test_me_without_a_token_is_401(client: httpx.AsyncClient) -> None:
    assert (await client.get("/auth/me")).status_code == 401


async def test_me_with_a_garbage_token_is_401(client: httpx.AsyncClient) -> None:
    response = await client.get("/auth/me", headers={"Authorization": "Bearer not.a.token"})
    assert response.status_code == 401


async def test_tampered_token_is_rejected(client: httpx.AsyncClient) -> None:
    await register(client)
    tokens = await login(client)
    header, payload, signature = tokens["access_token"].split(".")
    forged = f"{header}.{payload}.{signature[:-4]}AAAA"
    response = await client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


async def test_refresh_token_is_not_accepted_as_an_access_token(
    client: httpx.AsyncClient,
) -> None:
    await register(client)
    tokens = await login(client)
    response = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {tokens['refresh_token']}"}
    )
    assert response.status_code == 401


# --- refresh and logout ---------------------------------------------------


async def test_refresh_issues_a_new_pair(client: httpx.AsyncClient) -> None:
    await register(client)
    tokens = await login(client)
    response = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 200
    assert response.json()["refresh_token"] != tokens["refresh_token"]


async def test_old_refresh_token_stops_working_after_rotation(
    client: httpx.AsyncClient,
) -> None:
    await register(client)
    tokens = await login(client)
    await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    replay = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert replay.status_code == 401


async def test_replaying_a_used_token_kills_every_session(client: httpx.AsyncClient) -> None:
    """Reuse means the token leaked, so every live session is revoked."""
    await register(client)
    first = await login(client)
    rotated = (
        await client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    ).json()

    # Replay the spent token.
    await client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})

    # The token issued by the legitimate rotation must now be dead too.
    response = await client.post("/auth/refresh", json={"refresh_token": rotated["refresh_token"]})
    assert response.status_code == 401


async def test_unknown_refresh_token_is_401(client: httpx.AsyncClient) -> None:
    response = await client.post("/auth/refresh", json={"refresh_token": "made-up"})
    assert response.status_code == 401


async def test_logout_revokes_the_refresh_token(client: httpx.AsyncClient) -> None:
    await register(client)
    tokens = await login(client)
    logout = await client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert logout.status_code == 204

    after = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert after.status_code == 401


async def test_logout_with_an_unknown_token_is_still_204(client: httpx.AsyncClient) -> None:
    """Logout must not reveal whether a token existed."""
    response = await client.post("/auth/logout", json={"refresh_token": "never-existed"})
    assert response.status_code == 204

