from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import text

from app.core.db import SessionLocal, engine
from app.main import app


@pytest.fixture(autouse=True)
async def clean_tables() -> AsyncIterator[None]:
    """Start every test from an empty database."""
    async with SessionLocal() as session:
        await session.execute(text("TRUNCATE users, refresh_tokens CASCADE"))
        await session.execute(text("TRUNCATE users, refresh_tokens, listings CASCADE"))
        await session.commit()
    yield


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """Talks to the app in-process — no server, no port, no network."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(scope="session", autouse=True)
async def _dispose_engine() -> AsyncIterator[None]:
    yield
    await engine.dispose()
