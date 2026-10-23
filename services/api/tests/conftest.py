import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

# Tests can run against their own database. When TEST_DATABASE_URL is set, it
# replaces DATABASE_URL before the app is imported. The running API and relay keep
# the main database, so the tests never share rows with them. Advisory locks are
# scoped to a database too, so the relay's lock tests need this as well.
_test_database_url = os.environ.get("TEST_DATABASE_URL")
if _test_database_url:
    os.environ["DATABASE_URL"] = _test_database_url

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _migrate_database() -> None:
    """Bring the database the tests use up to the newest migration before any test runs."""
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
    )


@pytest.fixture(autouse=True)
async def clean_tables() -> AsyncIterator[None]:
    """Start every test from an empty database."""
    async with SessionLocal() as session:
        await session.execute(
            text("TRUNCATE users, refresh_tokens, listings, bids, orders, outbox_events CASCADE")
        )
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