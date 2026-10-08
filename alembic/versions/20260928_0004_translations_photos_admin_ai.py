"""Добавление source_language, статусов проверки переводов, multi-photo meta и лимита admin-переводов."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260928_0004"
down_revision = "20260928_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "excursions",
        sa.Column("source_language", sa.String(length=2), nullable=True),
    )
    op.create_check_constraint(
        "ck_excursions_source_language",
        "excursions",
        "source_language IS NULL OR source_language IN ('ru', 'en', 'it')",
    )

    op.add_column(
        "excursion_translations",
        sa.Column(
            "review_status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'confirmed'"),
        ),
    )
    op.add_column(
        "excursion_translations",
        sa.Column(
            "origin",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'manual'"),
        ),
    )
    op.add_column(
        "excursion_translations",
        sa.Column("source_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        "ck_excursion_translations_review_status",
        "excursion_translations",
        "review_status IN ('confirmed', 'pending_review', 'stale')",
    )
    op.create_check_constraint(
        "ck_excursion_translations_origin",
        "excursion_translations",
        "origin IN ('manual', 'ai', 'seed')",
    )

    op.create_table(
        "ai_daily_admin_translation_usage",
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint(
            "usage_date",
            "telegram_user_id",
            name="pk_ai_daily_admin_translation_usage",
        ),
    )


def downgrade() -> None:
    op.drop_table("ai_daily_admin_translation_usage")
    op.drop_constraint(
        "ck_excursion_translations_origin",
        "excursion_translations",
        type_="check",
    )
    op.drop_constraint(
        "ck_excursion_translations_review_status",
        "excursion_translations",
        type_="check",
    )
    op.drop_column("excursion_translations", "source_fingerprint")
    op.drop_column("excursion_translations", "origin")
    op.drop_column("excursion_translations", "review_status")
    op.drop_constraint("ck_excursions_source_language", "excursions", type_="check")
    op.drop_column("excursions", "source_language")
