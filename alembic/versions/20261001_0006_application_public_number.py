"""Публичный номер заявки (sequence) и UNIQUE.

Revision ID: 20261001_0006
Revises: 20260930_0005
Create Date: 2026-10-01 20:20:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261001_0006"
down_revision = "20260930_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("CREATE SEQUENCE applications_public_number_seq"))
    op.add_column(
        "applications",
        sa.Column("public_number", sa.Integer(), nullable=True),
    )
    # Стабильный порядок: по дате создания, затем по id.
    op.execute(
        sa.text(
            """
            WITH ordered AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           ORDER BY created_at ASC, id ASC
                       ) AS rn
                FROM applications
            )
            UPDATE applications AS a
            SET public_number = ordered.rn
            FROM ordered
            WHERE a.id = ordered.id
            """
        )
    )
    op.execute(
        sa.text(
            """
            SELECT setval(
                'applications_public_number_seq',
                COALESCE((SELECT MAX(public_number) FROM applications), 1),
                (SELECT EXISTS (SELECT 1 FROM applications WHERE public_number IS NOT NULL))
            )
            """
        )
    )
    op.alter_column(
        "applications",
        "public_number",
        existing_type=sa.Integer(),
        nullable=False,
        server_default=sa.text("nextval('applications_public_number_seq')"),
    )
    op.execute(
        sa.text(
            "ALTER SEQUENCE applications_public_number_seq "
            "OWNED BY applications.public_number"
        )
    )
    op.create_unique_constraint(
        "uq_applications_public_number",
        "applications",
        ["public_number"],
    )
    op.create_check_constraint(
        "ck_applications_public_number_positive",
        "applications",
        "public_number > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_applications_public_number_positive",
        "applications",
        type_="check",
    )
    op.drop_constraint(
        "uq_applications_public_number",
        "applications",
        type_="unique",
    )
    op.drop_column("applications", "public_number")
    op.execute(sa.text("DROP SEQUENCE IF EXISTS applications_public_number_seq"))
