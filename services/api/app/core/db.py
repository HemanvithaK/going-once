from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Parent of every ORM model. Collects table definitions for Alembic."""


_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
    pool_pre_ping=True,
    echo=False,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """One session per request: commit on success, roll back on any error."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
