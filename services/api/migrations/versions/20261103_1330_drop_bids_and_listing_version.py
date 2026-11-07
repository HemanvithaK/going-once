"""drop the bids table and listings.version

Bidding owns bids now, in its own database, and announces every change to an auction
itself. The core API no longer takes bids, and the version it bumped on each change is
always 0, so both are dead weight.

Downgrade recreates the shapes, not the rows. Any bids this database held before the
split are not recoverable from here.

Revision ID: 7b2e5f1a9c04
Revises: c41a7e0d9b52
Create Date: 2026-11-03 13:30:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7b2e5f1a9c04"
down_revision: str | None = "c41a7e0d9b52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_bids_listing_max_amount", table_name="bids")
    op.drop_index(op.f("ix_bids_listing_id"), table_name="bids")
    op.drop_index(op.f("ix_bids_bidder_id"), table_name="bids")
    op.drop_table("bids")
    op.drop_column("listings", "version")


def downgrade() -> None:
    op.add_column(
        "listings",
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.create_table(
        "bids",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
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
        sa.ForeignKeyConstraint(["bidder_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["listing_id"], ["listings.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bidder_id", "idempotency_key", name="uq_bids_bidder_idempotency"),
    )
    op.create_index(op.f("ix_bids_bidder_id"), "bids", ["bidder_id"], unique=False)
    op.create_index(op.f("ix_bids_listing_id"), "bids", ["listing_id"], unique=False)
    op.create_index(
        "ix_bids_listing_max_amount", "bids", ["listing_id", "max_amount"], unique=False
    )
