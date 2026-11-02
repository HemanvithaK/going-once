"""Importing this package registers every table on Base.metadata."""

from app.models.auction import Auction
from app.models.bid import Bid
from app.models.outbox import OutboxEvent

__all__ = ["Auction", "Bid", "OutboxEvent"]
