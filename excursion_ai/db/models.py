from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from excursion_ai.db.base import Base


class ApplicationStatus(str, enum.Enum):
    NEW = "new"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class ClientNotifyStatus(str, enum.Enum):
    NONE = "none"
    PENDING = "pending"
    SENT = "sent"
    ERROR = "error"


class ClientNotifyKind(str, enum.Enum):
    NONE = "none"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class CrmSyncStatus(str, enum.Enum):
    NOT_CONNECTED = "not_connected"
    PENDING = "pending"
    SENT = "sent"
    ERROR = "error"
    NEEDS_REVIEW = "needs_review"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("language IN ('ru', 'en', 'it')", name="ck_users_language"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    language: Mapped[str | None] = mapped_column(String(2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    applications: Mapped[list[Application]] = relationship(back_populates="user")


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("submission_key", name="uq_applications_submission_key"),
        CheckConstraint(
            "language IN ('ru', 'en', 'it')",
            name="ck_applications_language",
        ),
        CheckConstraint(
            "participants_count > 0",
            name="ck_applications_participants_positive",
        ),
        CheckConstraint(
            "unit_price >= 0",
            name="ck_applications_unit_price_nonnegative",
        ),
        CheckConstraint(
            "status IN ('new', 'confirmed', 'cancelled')",
            name="ck_applications_status",
        ),
        CheckConstraint(
            "crm_sync_status IN ("
            "'not_connected', 'pending', 'sent', 'error', 'needs_review')",
            name="ck_applications_crm_sync_status",
        ),
        CheckConstraint(
            "crm_sync_attempts >= 0",
            name="ck_applications_crm_sync_attempts",
        ),
        UniqueConstraint("public_number", name="uq_applications_public_number"),
        CheckConstraint(
            "public_number > 0",
            name="ck_applications_public_number_positive",
        ),
        CheckConstraint(
            "client_notify_status IN ('none', 'pending', 'sent', 'error')",
            name="ck_applications_client_notify_status",
        ),
        CheckConstraint(
            "client_notify_kind IN ('none', 'confirmed', 'cancelled')",
            name="ck_applications_client_notify_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    public_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("nextval('applications_public_number_seq')"),
    )
    submission_key: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    excursion_id: Mapped[str] = mapped_column(String(64), nullable=False)
    excursion_title: Mapped[str] = mapped_column(String(255), nullable=False)
    language: Mapped[str] = mapped_column(String(2), nullable=False)
    requested_date: Mapped[date] = mapped_column(Date, nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("excursion_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    session_starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    participants_count: Mapped[int] = mapped_column(Integer, nullable=False)
    customer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'new'"),
    )
    client_notify_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'none'"),
    )
    client_notify_kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'none'"),
    )
    booking_conflict_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    booking_conflict_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    crm_sync_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'not_connected'"),
    )
    crm_contact_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    crm_lead_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    crm_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    crm_sync_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    crm_last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped[User] = relationship(back_populates="applications")
    session: Mapped[ExcursionSession | None] = relationship(back_populates="applications")


class ExcursionSession(Base):
    __tablename__ = "excursion_sessions"
    __table_args__ = (
        UniqueConstraint(
            "excursion_id",
            "starts_at",
            name="uq_excursion_sessions_excursion_starts",
        ),
        CheckConstraint("capacity > 0", name="ck_excursion_sessions_capacity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    excursion_id: Mapped[str] = mapped_column(
        ForeignKey("excursions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    applications: Mapped[list[Application]] = relationship(back_populates="session")


class CrmWebhookEvent(Base):
    __tablename__ = "crm_webhook_events"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_crm_webhook_events_event_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    lead_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[str] = mapped_column(String(64), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class ExcursionStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class ExcursionRow(Base):
    __tablename__ = "excursions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_excursions_status",
        ),
        CheckConstraint("duration_minutes > 0", name="ck_excursions_duration"),
        CheckConstraint("price_per_person >= 0", name="ck_excursions_price"),
        CheckConstraint("version >= 1", name="ck_excursions_version"),
        CheckConstraint(
            "source_language IS NULL OR source_language IN ('ru', 'en', 'it')",
            name="ck_excursions_source_language",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    city_id: Mapped[str] = mapped_column(String(64), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    price_per_person: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'EUR'"))
    # JSON-массив кодов языков проведения, например ["ru","en"].
    conducted_languages: Mapped[str] = mapped_column(String(64), nullable=False)
    source_language: Mapped[str | None] = mapped_column(String(2), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'draft'"),
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    # Отложенные правки опубликованной экскурсии до явного подтверждения (JSON).
    pending_patch: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    translations: Mapped[list[ExcursionTranslationRow]] = relationship(
        back_populates="excursion",
        cascade="all, delete-orphan",
    )
    photos: Mapped[list[ExcursionPhotoRow]] = relationship(
        back_populates="excursion",
        cascade="all, delete-orphan",
        order_by="ExcursionPhotoRow.sort_order",
    )


class ExcursionTranslationRow(Base):
    __tablename__ = "excursion_translations"
    __table_args__ = (
        UniqueConstraint("excursion_id", "language", name="uq_excursion_translations_lang"),
        CheckConstraint("language IN ('ru', 'en', 'it')", name="ck_excursion_translations_lang"),
        CheckConstraint(
            "review_status IN ('confirmed', 'pending_review', 'stale')",
            name="ck_excursion_translations_review_status",
        ),
        CheckConstraint(
            "origin IN ('manual', 'ai', 'seed')",
            name="ck_excursion_translations_origin",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    excursion_id: Mapped[str] = mapped_column(
        ForeignKey("excursions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    language: Mapped[str] = mapped_column(String(2), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    city_label: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # JSON-массив пунктов маршрута.
    route_json: Mapped[str] = mapped_column(Text, nullable=False)
    meeting_point: Mapped[str] = mapped_column(Text, nullable=False)
    review_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'confirmed'"),
    )
    origin: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'manual'"),
    )
    source_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)

    excursion: Mapped[ExcursionRow] = relationship(back_populates="translations")


class ExcursionPhotoRow(Base):
    __tablename__ = "excursion_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    excursion_id: Mapped[str] = mapped_column(
        ForeignKey("excursions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_id: Mapped[str] = mapped_column(String(512), nullable=False)
    file_unique_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    excursion: Mapped[ExcursionRow] = relationship(back_populates="photos")
