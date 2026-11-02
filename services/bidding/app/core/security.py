"""Checks the access tokens the core API issues. Bidding verifies them and never creates them."""

from typing import Any

import jwt

from app.core.config import get_settings

_settings = get_settings()

JWT_ALGORITHM = "HS256"


def decode_access_token(token: str) -> dict[str, Any]:
    """Verify the signature and expiry. Raises jwt.PyJWTError if the token is not valid."""
    payload: dict[str, Any] = jwt.decode(
        token,
        _settings.jwt_secret,
        algorithms=[JWT_ALGORITHM],
        options={"require": ["exp", "sub", "typ"]},
    )
    if payload.get("typ") != "access":
        raise jwt.InvalidTokenError("not an access token")
    return payload
