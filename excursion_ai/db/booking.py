"""Подтверждение / отмена брони с атомарной проверкой вместимости."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from excursion_ai.db.models import (
    Application,
    ApplicationStatus,
    ClientNotifyKind,
    ClientNotifyStatus,
    ExcursionSession,
    User,
)
from excursion_ai.db.sessions import SessionCapacityError, SessionRepository
from excursion_ai.log_context import log_event

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BookingResult:
    application: Application
    changed: bool
    conflict: bool
    notify_pending: bool
    code: str


class BookingService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        sessions: SessionRepository | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._sessions = sessions or SessionRepository(session_factory)

    async def get_by_crm_lead_id(self, lead_id: str) -> Application | None:
        async with self._session_factory() as db:
            return await db.scalar(
                select(Application).where(Application.crm_lead_id == str(lead_id))
            )

    async def confirm_booking(self, application_id: uuid.UUID) -> BookingResult:
        async with self._session_factory() as db:
            async with db.begin():
                app = await db.scalar(
                    select(Application)
                    .where(Application.id == application_id)
                    .with_for_update()
                )
                if app is None:
                    raise SessionCapacityError("application_not_found")

                if app.status == ApplicationStatus.CONFIRMED.value:
                    log_event(
                        logger,
                        action="booking.confirm",
                        stage="idempotent",
                        result="ok",
                        application_id=str(application_id),
                    )
                    return BookingResult(
                        application=app,
                        changed=False,
                        conflict=False,
                        notify_pending=app.client_notify_status
                        != ClientNotifyStatus.SENT.value
                        or app.client_notify_kind != ClientNotifyKind.CONFIRMED.value,
                        code="already_confirmed",
                    )

                if app.status == ApplicationStatus.CANCELLED.value:
                    return BookingResult(
                        application=app,
                        changed=False,
                        conflict=False,
                        notify_pending=False,
                        code="already_cancelled",
                    )

                if app.session_id is None:
                    app.booking_conflict_code = "session_missing"
                    app.booking_conflict_at = datetime.now(timezone.utc)
                    await db.flush()
                    log_event(
                        logger,
                        action="booking.confirm",
                        stage="capacity",
                        result="conflict",
                        level=logging.ERROR,
                        application_id=str(application_id),
                        error_code="session_missing",
                    )
                    return BookingResult(
                        application=app,
                        changed=False,
                        conflict=True,
                        notify_pending=False,
                        code="session_missing",
                    )

                try:
                    await self._sessions.assert_can_book(
                        session_id=app.session_id,
                        participants=int(app.participants_count),
                        db=db,
                        exclude_application_id=app.id,
                    )
                except SessionCapacityError as error:
                    app.booking_conflict_code = error.code
                    app.booking_conflict_at = datetime.now(timezone.utc)
                    await db.flush()
                    log_event(
                        logger,
                        action="booking.confirm",
                        stage="capacity",
                        result="conflict",
                        level=logging.ERROR,
                        application_id=str(application_id),
                        session_id=str(app.session_id),
                        error_code=error.code,
                    )
                    return BookingResult(
                        application=app,
                        changed=False,
                        conflict=True,
                        notify_pending=False,
                        code=error.code,
                    )

                app.status = ApplicationStatus.CONFIRMED.value
                app.booking_conflict_code = None
                app.booking_conflict_at = None
                app.client_notify_kind = ClientNotifyKind.CONFIRMED.value
                app.client_notify_status = ClientNotifyStatus.PENDING.value
                await db.flush()
                await db.refresh(app)
                log_event(
                    logger,
                    action="booking.confirm",
                    stage="save",
                    result="ok",
                    application_id=str(application_id),
                    session_id=str(app.session_id),
                    participants=app.participants_count,
                )
                return BookingResult(
                    application=app,
                    changed=True,
                    conflict=False,
                    notify_pending=True,
                    code="confirmed",
                )

    async def cancel_booking(self, application_id: uuid.UUID) -> BookingResult:
        async with self._session_factory() as db:
            async with db.begin():
                app = await db.scalar(
                    select(Application)
                    .where(Application.id == application_id)
                    .with_for_update()
                )
                if app is None:
                    raise SessionCapacityError("application_not_found")

                if app.status == ApplicationStatus.CANCELLED.value:
                    notify_pending = (
                        app.client_notify_kind == ClientNotifyKind.CANCELLED.value
                        and app.client_notify_status != ClientNotifyStatus.SENT.value
                    )
                    return BookingResult(
                        application=app,
                        changed=False,
                        conflict=False,
                        notify_pending=notify_pending,
                        code="already_cancelled",
                    )

                app.status = ApplicationStatus.CANCELLED.value
                app.client_notify_kind = ClientNotifyKind.CANCELLED.value
                app.client_notify_status = ClientNotifyStatus.PENDING.value
                app.booking_conflict_code = None
                await db.flush()
                await db.refresh(app)
                log_event(
                    logger,
                    action="booking.cancel",
                    stage="save",
                    result="ok",
                    application_id=str(application_id),
                    session_id=str(app.session_id) if app.session_id else "-",
                )
                return BookingResult(
                    application=app,
                    changed=True,
                    conflict=False,
                    notify_pending=True,
                    code="cancelled",
                )

    async def mark_notify_sent(
        self, application_id: uuid.UUID, *, kind: str
    ) -> None:
        async with self._session_factory() as db:
            async with db.begin():
                app = await db.scalar(
                    select(Application).where(Application.id == application_id)
                )
                if app is None:
                    return
                if app.client_notify_kind != kind:
                    return
                app.client_notify_status = ClientNotifyStatus.SENT.value

    async def mark_notify_error(self, application_id: uuid.UUID) -> None:
        async with self._session_factory() as db:
            async with db.begin():
                app = await db.scalar(
                    select(Application).where(Application.id == application_id)
                )
                if app is None:
                    return
                if app.client_notify_status == ClientNotifyStatus.SENT.value:
                    return
                app.client_notify_status = ClientNotifyStatus.ERROR.value

    async def list_notify_retry(self, *, limit: int = 20) -> list[Application]:
        async with self._session_factory() as db:
            rows = (
                await db.scalars(
                    select(Application)
                    .where(
                        Application.client_notify_status.in_(
                            [
                                ClientNotifyStatus.PENDING.value,
                                ClientNotifyStatus.ERROR.value,
                            ]
                        ),
                        Application.client_notify_kind.in_(
                            [
                                ClientNotifyKind.CONFIRMED.value,
                                ClientNotifyKind.CANCELLED.value,
                            ]
                        ),
                    )
                    .order_by(Application.updated_at.asc())
                    .limit(limit)
                )
            ).all()
            return list(rows)

    async def telegram_user_id_for(self, application: Application) -> int | None:
        async with self._session_factory() as db:
            user = await db.scalar(
                select(User).where(User.id == application.user_id)
            )
            return int(user.telegram_user_id) if user else None
