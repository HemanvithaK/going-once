import uuid
from collections.abc import Callable, Coroutine
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import decode_access_token
from app.models.user import User, UserRole

bearer_scheme = HTTPBearer(auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CredentialsDep = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(credentials: CredentialsDep, session: SessionDep) -> User:
    """Turn an Authorization header into the User it names, or raise 401."""
    if credentials is None:
        raise _UNAUTHORIZED

    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise _UNAUTHORIZED from exc

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise _UNAUTHORIZED from exc

    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None or not user.is_active:
        raise _UNAUTHORIZED
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*allowed: UserRole) -> Callable[[User], Coroutine[Any, Any, User]]:
    """Build a dependency that only lets the listed roles through."""

    async def checker(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient permissions",
            )
        return user

    return checker
