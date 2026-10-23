import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class OutboxEvent(Base):
    """An event waiting to go to Kafka. Written in the same transaction as its change."""

    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    topic: Mapped[str] = mapped_column(String(100))
    # Kafka keeps messages with the same key in order. We key by listing id.
    partition_key: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    # NULL until the relay has sent this event to Kafka.
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        # The relay's query: unsent events, oldest first. The partial index holds
        # only unsent rows, so it stays small however many sent rows pile up.
        Index(
            "ix_outbox_unpublished",
            "created_at",
            postgresql_where=text("published_at IS NULL"),
        ),
    )

    def __repr__(self) -> str:
        sent = self.published_at is not None
        return f"<OutboxEvent {self.topic} key={self.partition_key} sent={sent}>"