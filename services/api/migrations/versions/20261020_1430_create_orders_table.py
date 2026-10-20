"""create orders table

Revision ID: 500ecf93f429
Revises: 879fed5476d7
Create Date: 2026-10-20 14:30:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "500ecf93f429"
down_revision: str | None = "879fed5476d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=False),
        sa.Column("buyer_id", sa.Uuid(), nullable=False),
        sa.Column("seller_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name="ck_orders_amount_positive"),
        sa.ForeignKeyConstraint(["buyer_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["listing_id"], ["listings.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["seller_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("listing_id"),
    )
    op.create_index(op.f("ix_orders_buyer_id"), "orders", ["buyer_id"], unique=False)
    op.create_index(op.f("ix_orders_seller_id"), "orders", ["seller_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_orders_seller_id"), table_name="orders")
    op.drop_index(op.f("ix_orders_buyer_id"), table_name="orders")
    op.drop_table("orders")