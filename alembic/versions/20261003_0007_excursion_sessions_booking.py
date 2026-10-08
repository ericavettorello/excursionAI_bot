"""Расписание сеансов, снимок в заявках и статусы брони.

Revision ID: 20261003_0007
Revises: 20261001_0006
Create Date: 2026-10-03 18:30:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261003_0007"
down_revision = "20261001_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "excursion_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("excursion_id", sa.String(length=64), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["excursion_id"],
            ["excursions.id"],
            name="fk_excursion_sessions_excursion_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "excursion_id",
            "starts_at",
            name="uq_excursion_sessions_excursion_starts",
        ),
        sa.CheckConstraint("capacity > 0", name="ck_excursion_sessions_capacity"),
    )
    op.create_index(
        "ix_excursion_sessions_excursion_starts",
        "excursion_sessions",
        ["excursion_id", "starts_at"],
    )
    op.create_index(
        "ix_excursion_sessions_active_starts",
        "excursion_sessions",
        ["is_active", "starts_at"],
    )

    op.add_column(
        "applications",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "applications",
        sa.Column("session_starts_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "applications",
        sa.Column(
            "client_notify_status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'none'"),
        ),
    )
    op.add_column(
        "applications",
        sa.Column(
            "client_notify_kind",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'none'"),
        ),
    )
    op.add_column(
        "applications",
        sa.Column("booking_conflict_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "applications",
        sa.Column(
            "booking_conflict_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_applications_session_id",
        "applications",
        "excursion_sessions",
        ["session_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_applications_session_id", "applications", ["session_id"])
    op.create_index("ix_applications_crm_lead_id", "applications", ["crm_lead_id"])

    op.drop_constraint("ck_applications_status", "applications", type_="check")
    op.create_check_constraint(
        "ck_applications_status",
        "applications",
        "status IN ('new', 'confirmed', 'cancelled')",
    )
    op.create_check_constraint(
        "ck_applications_client_notify_status",
        "applications",
        "client_notify_status IN ('none', 'pending', 'sent', 'error')",
    )
    op.create_check_constraint(
        "ck_applications_client_notify_kind",
        "applications",
        "client_notify_kind IN ('none', 'confirmed', 'cancelled')",
    )

    op.create_table(
        "crm_webhook_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("event_key", sa.String(length=255), nullable=False),
        sa.Column("lead_id", sa.String(length=128), nullable=False),
        sa.Column("status_id", sa.Integer(), nullable=True),
        sa.Column("result", sa.String(length=64), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("event_key", name="uq_crm_webhook_events_event_key"),
    )
    op.create_index(
        "ix_crm_webhook_events_lead_id",
        "crm_webhook_events",
        ["lead_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_crm_webhook_events_lead_id", table_name="crm_webhook_events")
    op.drop_table("crm_webhook_events")

    op.drop_constraint(
        "ck_applications_client_notify_kind", "applications", type_="check"
    )
    op.drop_constraint(
        "ck_applications_client_notify_status", "applications", type_="check"
    )
    op.drop_constraint("ck_applications_status", "applications", type_="check")
    op.create_check_constraint(
        "ck_applications_status",
        "applications",
        "status IN ('new', 'cancelled')",
    )
    op.drop_index("ix_applications_crm_lead_id", table_name="applications")
    op.drop_index("ix_applications_session_id", table_name="applications")
    op.drop_constraint("fk_applications_session_id", "applications", type_="foreignkey")
    op.drop_column("applications", "booking_conflict_at")
    op.drop_column("applications", "booking_conflict_code")
    op.drop_column("applications", "client_notify_kind")
    op.drop_column("applications", "client_notify_status")
    op.drop_column("applications", "session_starts_at")
    op.drop_column("applications", "session_id")

    op.drop_index("ix_excursion_sessions_active_starts", table_name="excursion_sessions")
    op.drop_index(
        "ix_excursion_sessions_excursion_starts", table_name="excursion_sessions"
    )
    op.drop_table("excursion_sessions")
