"""add excursions catalog tables

Revision ID: 20260928_0003
Revises: 20260928_0002
Create Date: 2026-09-28 20:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260928_0003"
down_revision: str | None = "20260928_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "excursions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("city_id", sa.String(length=64), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("price_per_person", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="EUR", nullable=False),
        sa.Column("conducted_languages", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("pending_patch", sa.Text(), nullable=True),
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
            "status IN ('draft', 'published', 'archived')",
            name="ck_excursions_status",
        ),
        sa.CheckConstraint("duration_minutes > 0", name="ck_excursions_duration"),
        sa.CheckConstraint("price_per_person >= 0", name="ck_excursions_price"),
        sa.CheckConstraint("version >= 1", name="ck_excursions_version"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_excursions_status", "excursions", ["status"])

    op.create_table(
        "excursion_translations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("excursion_id", sa.String(length=64), nullable=False),
        sa.Column("language", sa.String(length=2), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("city_label", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("route_json", sa.Text(), nullable=False),
        sa.Column("meeting_point", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "language IN ('ru', 'en', 'it')",
            name="ck_excursion_translations_lang",
        ),
        sa.ForeignKeyConstraint(["excursion_id"], ["excursions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("excursion_id", "language", name="uq_excursion_translations_lang"),
    )
    op.create_index(
        "ix_excursion_translations_excursion_id",
        "excursion_translations",
        ["excursion_id"],
    )

    op.create_table(
        "excursion_photos",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("excursion_id", sa.String(length=64), nullable=False),
        sa.Column("file_id", sa.String(length=512), nullable=False),
        sa.Column("file_unique_id", sa.String(length=255), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["excursion_id"], ["excursions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_excursion_photos_excursion_id",
        "excursion_photos",
        ["excursion_id"],
    )


def downgrade() -> None:
    op.drop_table("excursion_photos")
    op.drop_table("excursion_translations")
    op.drop_index("ix_excursions_status", table_name="excursions")
    op.drop_table("excursions")
