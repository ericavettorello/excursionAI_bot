"""Расписание сеансов экскурсий и проверка свободных мест (Europe/Rome)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from excursion_ai.db.models import Application, ApplicationStatus, ExcursionSession
from excursion_ai.forms.validation import ROME_TZ, now_rome, tomorrow_rome
from excursion_ai.log_context import log_event

logger = logging.getLogger(__name__)


class SessionError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SessionValidationError(SessionError):
    pass


class SessionCapacityError(SessionError):
    pass


@dataclass(frozen=True)
class SessionAvailability:
    session: ExcursionSession
    occupied: int
    free: int

    @property
    def capacity(self) -> int:
        return int(self.session.capacity)


def combine_rome(day: date, hhmm: time) -> datetime:
    return datetime.combine(day, hhmm, tzinfo=ROME_TZ)


def format_rome_date(value: datetime | date) -> str:
    if isinstance(value, datetime):
        value = value.astimezone(ROME_TZ).date()
    return value.strftime("%d.%m.%Y")


def format_rome_time(value: datetime) -> str:
    return value.astimezone(ROME_TZ).strftime("%H:%M")


def format_rome_datetime(value: datetime) -> str:
    local = value.astimezone(ROME_TZ)
    return local.strftime("%d.%m.%Y %H:%M")


def parse_rome_time(raw: str) -> time:
    text = raw.strip()
    try:
        return datetime.strptime(text, "%H:%M").time().replace(second=0, microsecond=0)
    except ValueError as error:
        raise SessionValidationError("time_format") from error


def is_upcoming_rome(starts_at: datetime, *, now: datetime | None = None) -> bool:
    """Сеанс ещё не начался в Europe/Rome."""
    moment = now or now_rome()
    local = starts_at if starts_at.tzinfo is not None else starts_at.replace(tzinfo=ROME_TZ)
    return local.astimezone(ROME_TZ) >= moment.astimezone(ROME_TZ)


class SessionRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_by_id(self, session_id: uuid.UUID) -> ExcursionSession | None:
        async with self._session_factory() as db:
            return await db.scalar(
                select(ExcursionSession).where(ExcursionSession.id == session_id)
            )

    async def list_for_excursion(
        self,
        excursion_id: str,
        *,
        include_inactive: bool = False,
        from_rome_date: date | None = None,
        until_rome_date: date | None = None,
        from_starts_at: datetime | None = None,
    ) -> list[ExcursionSession]:
        """until_rome_date — исключительная верхняя граница (день не включается)."""
        async with self._session_factory() as db:
            stmt: Select[tuple[ExcursionSession]] = select(ExcursionSession).where(
                ExcursionSession.excursion_id == excursion_id
            )
            if not include_inactive:
                stmt = stmt.where(ExcursionSession.is_active.is_(True))
            if from_starts_at is not None:
                stmt = stmt.where(ExcursionSession.starts_at >= from_starts_at)
            elif from_rome_date is not None:
                start = combine_rome(from_rome_date, time(0, 0))
                stmt = stmt.where(ExcursionSession.starts_at >= start)
            if until_rome_date is not None:
                end = combine_rome(until_rome_date, time(0, 0))
                stmt = stmt.where(ExcursionSession.starts_at < end)
            stmt = stmt.order_by(ExcursionSession.starts_at.asc())
            return list((await db.scalars(stmt)).all())

    async def list_upcoming_active(
        self,
        excursion_id: str,
        *,
        now: datetime | None = None,
        limit: int = 40,
    ) -> list[ExcursionSession]:
        """Активные сеансы, которые ещё не начались (Europe/Rome)."""
        moment = now or now_rome()
        rows = await self.list_for_excursion(
            excursion_id,
            include_inactive=False,
            from_starts_at=moment,
        )
        return rows[:limit]

    async def create_session(
        self,
        *,
        excursion_id: str,
        starts_at: datetime,
        capacity: int,
        is_active: bool = True,
    ) -> ExcursionSession:
        if capacity <= 0:
            raise SessionValidationError("capacity_positive")
        if starts_at.tzinfo is None:
            raise SessionValidationError("starts_at_timezone_required")
        async with self._session_factory() as db:
            try:
                async with db.begin():
                    row = ExcursionSession(
                        excursion_id=excursion_id,
                        starts_at=starts_at,
                        capacity=capacity,
                        is_active=is_active,
                    )
                    db.add(row)
                    await db.flush()
                    await db.refresh(row)
                    log_event(
                        logger,
                        action="schedule.create",
                        stage="insert",
                        result="ok",
                        excursion_id=excursion_id,
                        session_id=str(row.id),
                        capacity=capacity,
                    )
                    return row
            except IntegrityError as error:
                raise SessionValidationError("session_duplicate") from error

    async def update_session(
        self,
        session_id: uuid.UUID,
        *,
        starts_at: datetime | None = None,
        capacity: int | None = None,
        is_active: bool | None = None,
    ) -> ExcursionSession:
        async with self._session_factory() as db:
            async with db.begin():
                row = await db.scalar(
                    select(ExcursionSession)
                    .where(ExcursionSession.id == session_id)
                    .with_for_update()
                )
                if row is None:
                    raise SessionValidationError("session_not_found")
                if capacity is not None:
                    if capacity <= 0:
                        raise SessionValidationError("capacity_positive")
                    occupied = await self._occupied_confirmed(db, session_id)
                    if capacity < occupied:
                        raise SessionCapacityError("capacity_below_confirmed")
                    row.capacity = capacity
                if starts_at is not None:
                    if starts_at.tzinfo is None:
                        raise SessionValidationError("starts_at_timezone_required")
                    if starts_at != row.starts_at:
                        linked = await db.scalar(
                            select(func.count())
                            .select_from(Application)
                            .where(Application.session_id == session_id)
                        )
                        if int(linked or 0) > 0:
                            raise SessionValidationError("session_has_applications")
                    row.starts_at = starts_at
                if is_active is not None:
                    row.is_active = is_active
                try:
                    await db.flush()
                except IntegrityError as error:
                    raise SessionValidationError("session_duplicate") from error
                await db.refresh(row)
                log_event(
                    logger,
                    action="schedule.update",
                    stage="save",
                    result="ok",
                    session_id=str(session_id),
                    capacity=row.capacity,
                    is_active=row.is_active,
                )
                return row

    async def count_active(self, excursion_id: str) -> int:
        async with self._session_factory() as db:
            value = await db.scalar(
                select(func.count())
                .select_from(ExcursionSession)
                .where(
                    ExcursionSession.excursion_id == excursion_id,
                    ExcursionSession.is_active.is_(True),
                )
            )
            return int(value or 0)

    async def count_applications(self, session_id: uuid.UUID) -> int:
        async with self._session_factory() as db:
            value = await db.scalar(
                select(func.count())
                .select_from(Application)
                .where(Application.session_id == session_id)
            )
            return int(value or 0)

    async def deactivate_session(self, session_id: uuid.UUID) -> ExcursionSession:
        """Мягко убирает сеанс из предложения клиентам (is_active=False)."""
        return await self.update_session(session_id, is_active=False)

    async def delete_session(self, session_id: uuid.UUID) -> None:
        """Убирает сеанс из предложения клиентам (мягкая деактивация, без DROP)."""
        await self.deactivate_session(session_id)

    async def occupied_confirmed(self, session_id: uuid.UUID) -> int:
        async with self._session_factory() as db:
            return await self._occupied_confirmed(db, session_id)

    async def _occupied_confirmed(
        self, db: AsyncSession, session_id: uuid.UUID
    ) -> int:
        value = await db.scalar(
            select(func.coalesce(func.sum(Application.participants_count), 0)).where(
                Application.session_id == session_id,
                Application.status == ApplicationStatus.CONFIRMED.value,
            )
        )
        return int(value or 0)

    async def availability_for(
        self,
        session_id: uuid.UUID,
        *,
        exclude_application_id: uuid.UUID | None = None,
    ) -> SessionAvailability | None:
        async with self._session_factory() as db:
            row = await db.scalar(
                select(ExcursionSession).where(ExcursionSession.id == session_id)
            )
            if row is None:
                return None
            occupied = await self._occupied_confirmed(db, session_id)
            if exclude_application_id is not None:
                # Исключаем саму заявку, если она уже confirmed (для смены участников).
                own = await db.scalar(
                    select(Application).where(Application.id == exclude_application_id)
                )
                if (
                    own is not None
                    and own.session_id == session_id
                    and own.status == ApplicationStatus.CONFIRMED.value
                ):
                    occupied = max(0, occupied - int(own.participants_count))
            free = max(0, int(row.capacity) - occupied)
            return SessionAvailability(session=row, occupied=occupied, free=free)

    async def list_available_for_participants(
        self,
        *,
        excursion_id: str,
        participants: int,
        from_rome_date: date | None = None,
        limit: int = 40,
        now: datetime | None = None,
    ) -> list[SessionAvailability]:
        if participants <= 0:
            raise SessionValidationError("participants_positive")
        moment = now or now_rome()
        if from_rome_date is None:
            sessions = await self.list_for_excursion(
                excursion_id,
                include_inactive=False,
                from_starts_at=moment,
            )
        else:
            sessions = await self.list_for_excursion(
                excursion_id,
                include_inactive=False,
                from_rome_date=from_rome_date,
            )
        result: list[SessionAvailability] = []
        for item in sessions:
            if not is_upcoming_rome(item.starts_at, now=moment):
                continue
            occupied = await self.occupied_confirmed(item.id)
            free = max(0, int(item.capacity) - occupied)
            if free >= participants:
                result.append(
                    SessionAvailability(session=item, occupied=occupied, free=free)
                )
            if len(result) >= limit:
                break
        return result

    async def available_dates(
        self,
        *,
        excursion_id: str,
        participants: int,
        from_rome_date: date | None = None,
        limit_dates: int = 14,
        now: datetime | None = None,
    ) -> list[date]:
        items = await self.list_available_for_participants(
            excursion_id=excursion_id,
            participants=participants,
            from_rome_date=from_rome_date,
            limit=200,
            now=now,
        )
        dates: list[date] = []
        seen: set[date] = set()
        for item in items:
            day = item.session.starts_at.astimezone(ROME_TZ).date()
            if day in seen:
                continue
            seen.add(day)
            dates.append(day)
            if len(dates) >= limit_dates:
                break
        return dates

    async def available_times_on_date(
        self,
        *,
        excursion_id: str,
        participants: int,
        day: date,
        now: datetime | None = None,
    ) -> list[SessionAvailability]:
        """Все активные будущие сеансы выбранного дня (несколько времён — отдельные слоты)."""
        if participants <= 0:
            raise SessionValidationError("participants_positive")
        moment = now or now_rome()
        day_sessions = await self.list_for_excursion(
            excursion_id,
            include_inactive=False,
            from_rome_date=day,
            until_rome_date=day + timedelta(days=1),
        )
        result: list[SessionAvailability] = []
        for item in day_sessions:
            if not is_upcoming_rome(item.starts_at, now=moment):
                continue
            occupied = await self.occupied_confirmed(item.id)
            free = max(0, int(item.capacity) - occupied)
            if free >= participants:
                result.append(
                    SessionAvailability(session=item, occupied=occupied, free=free)
                )
        return result

    async def assert_can_book(
        self,
        *,
        session_id: uuid.UUID,
        participants: int,
        db: AsyncSession | None = None,
        exclude_application_id: uuid.UUID | None = None,
    ) -> ExcursionSession:
        """Проверка вместимости. При переданном db — внутри текущей транзакции с FOR UPDATE."""

        async def _check(session: AsyncSession) -> ExcursionSession:
            row = await session.scalar(
                select(ExcursionSession)
                .where(ExcursionSession.id == session_id)
                .with_for_update()
            )
            if row is None:
                raise SessionValidationError("session_not_found")
            if not row.is_active:
                raise SessionCapacityError("session_inactive")
            if not is_upcoming_rome(row.starts_at):
                raise SessionCapacityError("session_past")
            occupied = await self._occupied_confirmed(session, session_id)
            if exclude_application_id is not None:
                own = await session.scalar(
                    select(Application).where(Application.id == exclude_application_id)
                )
                if (
                    own is not None
                    and own.session_id == session_id
                    and own.status == ApplicationStatus.CONFIRMED.value
                ):
                    occupied = max(0, occupied - int(own.participants_count))
            if occupied + participants > int(row.capacity):
                raise SessionCapacityError("not_enough_seats")
            return row

        if db is not None:
            return await _check(db)
        async with self._session_factory() as session:
            async with session.begin():
                return await _check(session)
