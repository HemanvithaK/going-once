import uuid
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status

from app.api.deps import BidderDep, SessionDep
from app.schemas.bid import BidCreate, BidOut
from app.services.bidding import BidError, place_bid

# The same path the core API uses. Moving traffic to this service is then a routing
# change, not a client change.
router = APIRouter(prefix="/listings", tags=["bids"])


@router.post(
    "/{listing_id}/bids",
    response_model=BidOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_bid(
    listing_id: uuid.UUID,
    body: BidCreate,
    bidder_id: BidderDep,
    session: SessionDep,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=64),
    ],
) -> BidOut:
    try:
        result = await place_bid(session, bidder_id, listing_id, body.max_amount, idempotency_key)
    except BidError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return BidOut(
        id=result.bid.id,
        listing_id=result.auction.listing_id,
        max_amount=result.bid.max_amount,
        current_price=result.auction.current_price,
        is_leading=result.is_leading,
        created_at=result.bid.created_at,
    )
