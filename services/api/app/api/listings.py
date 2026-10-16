import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, SessionDep, bearer_scheme, get_current_user
from app.api.pagination import decode_cursor, encode_cursor
from app.core.logging import get_logger
from app.models.listing import Listing, ListingStatus
from app.models.user import User
from app.schemas.listing import (
    ListingCreate,
    ListingOut,
    ListingPage,
    ListingUpdate,
    listing_out,
)

router = APIRouter(prefix="/listings", tags=["listings"])
log = get_logger(__name__)

# Starlette renamed this constant between versions, so use the number.
_UNPROCESSABLE = 422

_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="listing not found")

# Statuses a buyer is allowed to see. A draft belongs to its seller alone.
_PUBLIC_STATUSES = (
    ListingStatus.SCHEDULED,
    ListingStatus.LIVE,
    ListingStatus.SOLD,
    ListingStatus.UNSOLD,
)


async def optional_user(
    credentials: Annotated[object | None, Depends(bearer_scheme)],
    session: SessionDep,
) -> User | None:
    """The caller if they sent a valid token, otherwise None. Never raises."""
    if credentials is None:
        return None
    try:
        return await get_current_user(credentials, session)  # type: ignore[arg-type]
    except HTTPException:
        return None


OptionalUser = Annotated[User | None, Depends(optional_user)]


async def _get_visible_listing(
    session: AsyncSession, listing_id: uuid.UUID, viewer: User | None
) -> Listing:
    """Load a listing the viewer is allowed to see, or 404."""
    listing = await session.scalar(select(Listing).where(Listing.id == listing_id))
    if listing is None:
        raise _not_found()
    is_seller = viewer is not None and viewer.id == listing.seller_id
    if listing.status not in _PUBLIC_STATUSES and not is_seller:
        # 404, not 403: a stranger should not learn that a draft exists.
        raise _not_found()
    return listing

def _not_found() -> HTTPException:
    """A fresh 404 per request: a shared exception object keeps growing its traceback."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="listing not found")

async def _get_own_listing(session: AsyncSession, listing_id: uuid.UUID, user: User) -> Listing:
    """Load a listing the caller owns, or 404."""
    listing = await session.scalar(select(Listing).where(Listing.id == listing_id))
    if listing is None or listing.seller_id != user.id:
        raise _not_found()
    return listing


@router.post("", response_model=ListingOut, status_code=status.HTTP_201_CREATED)
async def create_listing(body: ListingCreate, user: CurrentUser, session: SessionDep) -> ListingOut:
    listing = Listing(
        seller_id=user.id,
        title=body.title,
        description=body.description,
        start_price=body.start_price,
        reserve_price=body.reserve_price,
        bid_increment=body.bid_increment,
        current_price=body.start_price,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        status=ListingStatus.DRAFT,
    )
    session.add(listing)
    await session.flush()
    log.info("listing_created", listing_id=str(listing.id), seller_id=str(user.id))
    return listing_out(listing, user)


@router.get("", response_model=ListingPage)
async def browse_listings(
    session: SessionDep,
    viewer: OptionalUser,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: str | None = None,
) -> ListingPage:
    """Live listings, soonest to close first, cursor-paginated."""
    query = select(Listing).where(Listing.status == ListingStatus.LIVE)

    if cursor is not None:
        try:
            last_ends_at, last_id = decode_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(
                status_code=_UNPROCESSABLE,
                detail="malformed cursor",
            ) from exc
        # Row-value comparison: "everything after this (ends_at, id) pair".
        query = query.where(tuple_(Listing.ends_at, Listing.id) > (last_ends_at, last_id))

    # Ask for one more than requested: its existence is how we know
    # whether another page exists, without a second COUNT query.
    rows = list(await session.scalars(query.order_by(Listing.ends_at, Listing.id).limit(limit + 1)))

    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = encode_cursor(page[-1].ends_at, page[-1].id) if has_more else None

    return ListingPage(
        items=[listing_out(row, viewer) for row in page],
        next_cursor=next_cursor,
    )


@router.get("/mine", response_model=list[ListingOut])
async def my_listings(user: CurrentUser, session: SessionDep) -> list[ListingOut]:
    """Everything the caller has listed, drafts included."""
    rows = await session.scalars(
        select(Listing).where(Listing.seller_id == user.id).order_by(Listing.created_at.desc())
    )
    return [listing_out(row, user) for row in rows]


@router.get("/{listing_id}", response_model=ListingOut)
async def get_listing(
    listing_id: uuid.UUID, session: SessionDep, viewer: OptionalUser
) -> ListingOut:
    listing = await _get_visible_listing(session, listing_id, viewer)
    return listing_out(listing, viewer)


@router.patch("/{listing_id}", response_model=ListingOut)
async def update_listing(
    listing_id: uuid.UUID, body: ListingUpdate, user: CurrentUser, session: SessionDep
) -> ListingOut:
    listing = await _get_own_listing(session, listing_id, user)

    if listing.status is not ListingStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="only a draft listing can be edited",
        )

    # exclude_unset: a field the client never sent is left alone, which is
    # what makes PATCH different from PUT.
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(listing, field, value)

    if listing.ends_at <= listing.starts_at:
        raise HTTPException(
            status_code=_UNPROCESSABLE,
            detail="ends_at must be after starts_at",
        )
    if listing.reserve_price is not None and listing.reserve_price < listing.start_price:
        raise HTTPException(
            status_code=_UNPROCESSABLE,
            detail="reserve_price cannot be below start_price",
        )
    listing.current_price = listing.start_price

    await session.flush()
    return listing_out(listing, user)


@router.post("/{listing_id}/publish", response_model=ListingOut)
async def publish_listing(
    listing_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> ListingOut:
    """Draft → scheduled, or straight to live if it has already started."""
    listing = await _get_own_listing(session, listing_id, user)

    if listing.status is not ListingStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="only a draft listing can be published",
        )

    now = datetime.now(UTC)
    if listing.ends_at <= now:
        raise HTTPException(
            status_code=_UNPROCESSABLE,
            detail="cannot publish a listing that has already ended",
        )

    listing.status = ListingStatus.LIVE if listing.starts_at <= now else ListingStatus.SCHEDULED
    await session.flush()
    log.info("listing_published", listing_id=str(listing.id), status=listing.status.value)
    return listing_out(listing, user)