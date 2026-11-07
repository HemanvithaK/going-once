"""The pricing rule, checked against an independent model over many random bid sequences.

The rule, in one line: the leader pays the best rival ceiling plus one increment, never
more than their own ceiling. With no rival, the price is the start price. The price must
depend on who bid what, not on the order the bids arrived in.

The model below is written from that sentence, not from the code under test. Each bid is
sent through the real route, and every response and every stored value is compared with
what the model says.
"""

import json
import random
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

import httpx
import pytest
from auction_contracts.events import AUCTION_OUTCOMES_TOPIC, AUCTION_UPDATES_TOPIC, parse_event
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import Auction, Bid, OutboxEvent
from app.services.closing import close_all_due
from tests.helpers import make_due, open_auction, place

START = Decimal("100.00")
INCREMENT = Decimal("5.00")


@dataclass
class Model:
    """What the rule says, kept as plain data: each bidder's ceiling and when they reached it."""

    ceilings: dict[uuid.UUID, tuple[Decimal, int]] = field(default_factory=dict)
    clock: int = 0

    def leader(self) -> uuid.UUID | None:
        if not self.ceilings:
            return None
        # Highest ceiling wins. A tie goes to whoever reached that ceiling first.
        return max(self.ceilings, key=lambda b: (self.ceilings[b][0], -self.ceilings[b][1]))

    def price(self) -> Decimal:
        leader = self.leader()
        if leader is None:
            return START
        rivals = [c for bidder, (c, _) in self.ceilings.items() if bidder != leader]
        if not rivals:
            return START
        return min(self.ceilings[leader][0], max(rivals) + INCREMENT)

    def minimum_for(self, bidder: uuid.UUID) -> Decimal:
        leader = self.leader()
        if leader is None:
            return START
        if leader == bidder:
            return self.ceilings[leader][0] + INCREMENT
        return self.price() + INCREMENT

    def accept(self, bidder: uuid.UUID, amount: Decimal) -> None:
        self.clock += 1
        self.ceilings[bidder] = (amount, self.clock)


async def read_auction(listing_id: uuid.UUID) -> Auction:
    async with SessionLocal() as session:
        auction = await session.get(Auction, listing_id)
    assert auction is not None
    return auction


async def count_bids() -> int:
    async with SessionLocal() as session:
        return len(list(await session.scalars(select(Bid.id))))


async def events(topic: str) -> list[dict[str, object]]:
    async with SessionLocal() as session:
        rows = await session.scalars(
            select(OutboxEvent).where(OutboxEvent.topic == topic).order_by(OutboxEvent.created_at)
        )
        return [dict(row.payload) for row in rows]


@pytest.mark.parametrize("seed", range(12))
async def test_random_bid_sequences_follow_the_pricing_rule(
    client: httpx.AsyncClient, seed: int
) -> None:
    rng = random.Random(seed)
    listing_id, _ = await open_auction(start=str(START), increment=str(INCREMENT))
    bidders = [uuid.uuid4() for _ in range(rng.randint(2, 4))]
    model = Model()
    accepted = 0

    for step in range(rng.randint(8, 14)):
        bidder = rng.choice(bidders)
        amount = Decimal(rng.randint(95, 260))
        before = await read_auction(listing_id)
        bids_before = await count_bids()
        label = f"seed {seed}, step {step}: {bidder.hex[:4]} bids {amount}"

        response = await place(client, listing_id, bidder, f"{amount:.2f}", f"k-{seed}-{step:04d}")

        should_accept = amount >= model.minimum_for(bidder)
        assert response.status_code == (201 if should_accept else 409), label
        after = await read_auction(listing_id)
        if not should_accept:
            # A refused bid changes nothing: no bid row, no price move, no new version.
            assert await count_bids() == bids_before, label
            assert (after.current_price, after.version) == (before.current_price, before.version)
            continue

        model.accept(bidder, amount)
        accepted += 1
        assert await count_bids() == bids_before + 1, label
        assert after.current_price == model.price(), label
        assert after.current_price >= before.current_price, label  # the price never drops
        assert after.version == accepted, label
        assert response.json()["is_leading"] == (model.leader() == bidder), label
        assert Decimal(response.json()["current_price"]) == model.price(), label

    # Every accepted bid announced itself once, with consecutive versions.
    updates = await events(AUCTION_UPDATES_TOPIC)
    assert [e["version"] for e in updates] == list(range(1, accepted + 1))

    # Closing settles to the model's winner at the model's price.
    await make_due(listing_id)
    assert await close_all_due() == 1
    [settled] = [parse_event(json.dumps(e)) for e in await events(AUCTION_OUTCOMES_TOPIC)]
    if model.leader() is None:
        assert settled.status == "unsold"  # type: ignore[union-attr]
    else:
        assert settled.status == "sold"  # type: ignore[union-attr]
        assert settled.winner_id == model.leader()  # type: ignore[union-attr]
        assert settled.final_price == model.price()  # type: ignore[union-attr]


async def test_the_price_does_not_depend_on_the_order_the_bids_arrived_in(
    client: httpx.AsyncClient,
) -> None:
    """Same ceilings, two arrival orders, one price."""
    alice, bob = uuid.uuid4(), uuid.uuid4()

    # Order one: Alice 150, Bob 148, then Alice raises to 200.
    first, _ = await open_auction(start=str(START), increment=str(INCREMENT))
    assert (await place(client, first, alice, "150.00", "order-one-a1")).status_code == 201
    assert (await place(client, first, bob, "148.00", "order-one-b1")).status_code == 201
    assert (await place(client, first, alice, "200.00", "order-one-a2")).status_code == 201

    # Order two: Alice 200, then Bob 148.
    second, _ = await open_auction(start=str(START), increment=str(INCREMENT))
    assert (await place(client, second, alice, "200.00", "order-two-a1")).status_code == 201
    assert (await place(client, second, bob, "148.00", "order-two-b1")).status_code == 201

    one, two = await read_auction(first), await read_auction(second)
    assert one.current_price == two.current_price == Decimal("153.00")
