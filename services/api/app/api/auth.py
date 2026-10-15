from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, SessionDep
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    password_needs_rehash,
    refresh_token_expiry,
    verify_password,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])
log = get_logger(__name__)
_settings = get_settings()

_BAD_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="invalid email or password",
)
_BAD_REFRESH = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="invalid or expired refresh token",
)


async def _issue_tokens(session: SessionDep, user: User) -> TokenPair:
    """Create one access token and one stored refresh token for a user."""
    raw_refresh, refresh_hash = new_refresh_token()
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=refresh_hash,
            expires_at=refresh_token_expiry(),
        )
    )
    return TokenPair(
        access_token=create_access_token(user.id, user.role.value),
        refresh_token=raw_refresh,
        expires_in=_settings.access_token_minutes * 60,
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, session: SessionDep) -> User:
    user = User(
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        display_name=body.display_name,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="email already registered",
        ) from exc

    log.info("user_registered", user_id=str(user.id))
    return user


@router.post("/login", response_model=TokenPair)
async def login(body: LoginRequest, session: SessionDep) -> TokenPair:
    user = await session.scalar(select(User).where(User.email == body.email.lower()))

    if user is None or not verify_password(user.password_hash, body.password):
        raise _BAD_CREDENTIALS
    if not user.is_active:
        raise _BAD_CREDENTIALS

    # Opportunistic upgrade: if this hash used weaker parameters than we use
    # now, replace it while we have the plaintext in hand.
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)

    log.info("user_logged_in", user_id=str(user.id))
    return await _issue_tokens(session, user)


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest, session: SessionDep) -> TokenPair:
    presented = hash_refresh_token(body.refresh_token)
    token = await session.scalar(select(RefreshToken).where(RefreshToken.token_hash == presented))

    if token is None:
        raise _BAD_REFRESH

    # A token that was already used or revoked is being replayed. Assume it
    # was stolen and kill every session this user has.
    if token.revoked_at is not None:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == token.user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        # Commit before raising: get_session rolls back on an exception, and
        # this revocation has to survive the 401 we are about to return.
        await session.commit()
        log.warning("refresh_token_reuse_detected", user_id=str(token.user_id))
        raise _BAD_REFRESH

    if token.expires_at <= datetime.now(UTC):
        raise _BAD_REFRESH

    user = await session.scalar(select(User).where(User.id == token.user_id))
    if user is None or not user.is_active:
        raise _BAD_REFRESH

    # Rotation: this token is spent the moment it is used.
    token.revoked_at = datetime.now(UTC)
    return await _issue_tokens(session, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, session: SessionDep) -> Response:
    await session.execute(
        update(RefreshToken)
        .where(
            RefreshToken.token_hash == hash_refresh_token(body.refresh_token),
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
    # Always 204: whether that token existed is not the caller's business.
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user
