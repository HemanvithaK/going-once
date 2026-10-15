import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

_settings = get_settings()

# OWASP minimum for Argon2id: 19 MiB of memory, 2 passes, 1 thread.
_hasher = PasswordHasher(memory_cost=19456, time_cost=2, parallelism=1)

JWT_ALGORITHM = "HS256"


# --- passwords ------------------------------------------------------------


def hash_password(plain: str) -> str:
    """Turn a plaintext password into a storable argon2 hash."""
    return _hasher.hash(plain)


def verify_password(stored_hash: str, plain: str) -> bool:
    """True if the password matches. Never raises on a wrong password."""
    try:
        _hasher.verify(stored_hash, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return True


def password_needs_rehash(stored_hash: str) -> bool:
    """True when a hash was made with weaker parameters than we use now."""
    return _hasher.check_needs_rehash(stored_hash)


# --- access tokens --------------------------------------------------------


def create_access_token(user_id: uuid.UUID, role: str) -> str:
    """A short-lived signed token proving who the caller is."""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "typ": "access",
        "iat": now,
        "exp": now + timedelta(minutes=_settings.access_token_minutes),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, _settings.jwt_secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Verify the signature and expiry. Raises jwt.PyJWTError if invalid."""
    payload: dict[str, Any] = jwt.decode(
        token,
        _settings.jwt_secret,
        algorithms=[JWT_ALGORITHM],
        options={"require": ["exp", "sub", "typ"]},
    )
    if payload.get("typ") != "access":
        raise jwt.InvalidTokenError("not an access token")
    return payload


# --- refresh tokens -------------------------------------------------------


def new_refresh_token() -> tuple[str, str]:
    """Return (raw token for the client, hash to store in the database)."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    """SHA-256 is fine here: the token is 256 random bits, not a password."""
    return hashlib.sha256(raw.encode()).hexdigest()


def refresh_token_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(days=_settings.refresh_token_days)
