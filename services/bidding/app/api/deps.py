import uuid
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)

# scope="function": get_session commits when the endpoint returns, before the response
# goes out. The default scope commits after the response has been sent.
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_bidder_id(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> uuid.UUID:
    """Read the bidder's id from the token. Bidding has no users table, so there is no lookup."""
    if credentials is None:
        raise _unauthorized()
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise _unauthorized() from exc
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise _unauthorized() from exc


BidderDep = Annotated[uuid.UUID, Depends(get_bidder_id)]
