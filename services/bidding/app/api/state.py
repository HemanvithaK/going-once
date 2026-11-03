"""Public auction state: what any viewer may know about an auction right now.

Realtime calls this when a viewer connects, to get the first snapshot. The answer has
the same shape as an auction-updates message, so a viewer's socket carries one kind of
message from the first frame to the last.
"""

import uuid

from auction_contracts.events import AuctionStatus, AuctionUpdated
from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import SessionDep
from app.models import Auction

router = APIRouter(prefix="/listings", tags=["state"])


@router.get("/{listing_id}/state", response_model=AuctionUpdated)
async def get_state(
    listing_id: uuid.UUID,
    response: Response,
    session: SessionDep,
) -> AuctionUpdated:
    """No login: the data is public. The model has no reserve, leader, or winner to leak."""
    auction = await session.get(Auction, listing_id)
    if auction is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="auction not found")

    # A cached snapshot would be an old price shown as the current one.
    response.headers["Cache-Control"] = "no-store"
    return AuctionUpdated(
        listing_id=auction.listing_id,
        version=auction.version,
        status=AuctionStatus(auction.status),
        current_price=auction.current_price,
        ends_at=auction.ends_at,
    )
