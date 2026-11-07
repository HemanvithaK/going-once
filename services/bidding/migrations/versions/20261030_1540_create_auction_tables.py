"""create auction and bid tables

Revision ID: 9b1f4c2d7e30
Revises:
Create Date: 2026-10-30 15:40:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9b1f4c2d7e30"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auctions",
        sa.Column("listing_id", sa.Uuid(), nullable=False),
        sa.Column("seller_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("start_price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("bid_increment", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("reserve_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("current_price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('live', 'sold', 'unsold')",
            name="ck_auctions_status_known",
        ),
        sa.CheckConstraint("start_price > 0", name="ck_auctions_start_price_positive"),
        sa.CheckConstraint("bid_increment > 0", name="ck_auctions_increment_positive"),
        sa.CheckConstraint(
            "reserve_price IS NULL OR reserve_price >= start_price",
            name="ck_auctions_reserve_at_least_start",
        ),
        sa.CheckConstraint(
            "current_price >= start_price",
            name="ck_auctions_current_at_least_start",
        ),
        sa.PrimaryKeyConstraint("listing_id"),
    )
    op.create_index(
        "ix_auctions_status_ends_at",
        "auctions",
        ["status", "ends_at"],
        unique=False,
    )

    op.create_table(
        "bids",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=False),
        sa.Column("bidder_id", sa.Uuid(), nullable=False),
        sa.Column("max_amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("max_amount > 0", name="ck_bids_max_amount_positive"),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["auctions.listing_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bidder_id", "idempotency_key", name="uq_bids_bidder_idempotency"),
    )
    op.create_index("ix_bids_bidder_id", "bids", ["bidder_id"], unique=False)
    op.create_index("ix_bids_listing_id", "bids", ["listing_id"], unique=False)
    op.create_index(
        "ix_bids_listing_max_amount",
        "bids",
        ["listing_id", "max_amount"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_bids_listing_max_amount", table_name="bids")
    op.drop_index("ix_bids_listing_id", table_name="bids")
    op.drop_index("ix_bids_bidder_id", table_name="bids")
    op.drop_table("bids")
    op.drop_index("ix_auctions_status_ends_at", table_name="auctions")
    op.drop_table("auctions")
