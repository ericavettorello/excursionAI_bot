"""add ai daily usage counters

Revision ID: 20260928_0002
Revises: 20260927_0001
Create Date: 2026-09-28 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260928_0002"
down_revision: str | None = "20260927_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_daily_user_usage",
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("request_count", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("request_count >= 0", name="ck_ai_daily_user_usage_count"),
        sa.PrimaryKeyConstraint("usage_date", "telegram_user_id"),
    )
    op.create_table(
        "ai_daily_global_usage",
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("request_count", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("request_count >= 0", name="ck_ai_daily_global_usage_count"),
        sa.PrimaryKeyConstraint("usage_date"),
    )


def downgrade() -> None:
    op.drop_table("ai_daily_global_usage")
    op.drop_table("ai_daily_user_usage")
