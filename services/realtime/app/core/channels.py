def auction_channel(listing_id: object) -> str:
    """The Redis channel for one auction.

    The prefix is "auction:", not "listing:". While the old viewer path still runs next
    to this one, the two must not share channels: they count versions separately.
    """
    return f"auction:{listing_id}"
