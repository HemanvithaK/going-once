import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    # SHA-256 hex is always 64 characters. We store the hash, never the token.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"<RefreshToken user={self.user_id} revoked={self.revoked_at is not None}>"
