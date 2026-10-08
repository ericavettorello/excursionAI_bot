"""Расширение CRM-синхронизации заявок: статусы, попытки, время синка.

Revision ID: 20260930_0005
Revises: 20260928_0004
Create Date: 2026-09-30 18:50:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260930_0005"
down_revision = "20260928_0004"
branch_labels = None
depends_on = None

_CRM_STATUSES = (
    "not_connected",
    "pending",
    "sent",
    "error",
    "needs_review",
)


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("crm_synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "applications",
        sa.Column(
            "crm_sync_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "applications",
        sa.Column("crm_last_error_code", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        "ck_applications_crm_sync_status",
        "applications",
        "crm_sync_status IN ("
        + ", ".join(f"'{item}'" for item in _CRM_STATUSES)
        + ")",
    )
    op.create_check_constraint(
        "ck_applications_crm_sync_attempts",
        "applications",
        "crm_sync_attempts >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_applications_crm_sync_attempts", "applications", type_="check"
    )
    op.drop_constraint(
        "ck_applications_crm_sync_status", "applications", type_="check"
    )
    op.drop_column("applications", "crm_last_error_code")
    op.drop_column("applications", "crm_sync_attempts")
    op.drop_column("applications", "crm_synced_at")
