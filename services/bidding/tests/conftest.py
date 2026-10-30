import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

# Tests can run against their own database. When TEST_DATABASE_URL is set, it replaces
# DATABASE_URL before the app is imported, so the tests never touch the running
# service's rows.
_test_database_url = os.environ.get("TEST_DATABASE_URL")
if _test_database_url:
    os.environ["DATABASE_URL"] = _test_database_url

import httpx
import pytest
from sqlalchemy import text

from app.core.db import SessionLocal, engine
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def _migrate_database() -> None:
    """Bring the test database up to the newest migration before any test runs."""
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
    )


@pytest.fixture(autouse=True)
async def clean_tables() -> AsyncIterator[None]:
    """Start every test from empty tables."""
    async with SessionLocal() as session:
        await session.execute(text("TRUNCATE bids, auctions CASCADE"))
        await session.commit()
    yield


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """Talks to the app in-process. No server, no port, no network."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(scope="session", autouse=True)
async def _dispose_engine() -> AsyncIterator[None]:
    yield
    await engine.dispose()