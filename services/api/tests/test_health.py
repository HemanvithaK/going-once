from collections.abc import AsyncIterator
from unittest.mock import patch

import httpx
import pytest

from app.main import app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """Talks to the app in-process — no server, no port, no network."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_healthz_is_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readyz_is_503_when_database_is_down(client: httpx.AsyncClient) -> None:
    """Readiness must fail — not raise — when the database is unreachable."""
    with patch("app.main.engine") as fake_engine:
        fake_engine.connect.side_effect = OSError("connection refused")
        response = await client.get("/readyz")
    assert response.status_code == 503