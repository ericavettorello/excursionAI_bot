"""create users and applications tables

Revision ID: 20260927_0001
Revises:
Create Date: 2026-09-27 19:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260927_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("language", sa.String(length=2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("language IN ('ru', 'en', 'it')", name="ck_users_language"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_user_id"),
    )

    op.create_table(
        "applications",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("submission_key", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("excursion_id", sa.String(length=64), nullable=False),
        sa.Column("excursion_title", sa.String(length=255), nullable=False),
        sa.Column("language", sa.String(length=2), nullable=False),
        sa.Column("requested_date", sa.Date(), nullable=False),
        sa.Column("participants_count", sa.Integer(), nullable=False),
        sa.Column("customer_name", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column(
            "is_demo",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("unit_price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'new'"),
            nullable=False,
        ),
        sa.Column(
            "crm_sync_status",
            sa.String(length=32),
            server_default=sa.text("'not_connected'"),
            nullable=False,
        ),
        sa.Column("crm_contact_id", sa.String(length=128), nullable=True),
        sa.Column("crm_lead_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "language IN ('ru', 'en', 'it')",
            name="ck_applications_language",
        ),
        sa.CheckConstraint(
            "participants_count > 0",
            name="ck_applications_participants_positive",
        ),
        sa.CheckConstraint(
            "unit_price >= 0",
            name="ck_applications_unit_price_nonnegative",
        ),
        sa.CheckConstraint(
            "status IN ('new', 'cancelled')",
            name="ck_applications_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("submission_key", name="uq_applications_submission_key"),
    )
    op.create_index("ix_applications_user_id", "applications", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_applications_user_id", table_name="applications")
    op.drop_table("applications")
    op.drop_table("users")
