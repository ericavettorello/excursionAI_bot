"""Тесты расписания, вместимости и обработки брони (mock / PostgreSQL)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

from excursion_ai.crm.inbound import extract_lead_status_events
from excursion_ai.crm.pipelines import find_status_ids_by_names
from excursion_ai.crm.client import AmoCrmApiError
from excursion_ai.config import load_settings
from excursion_ai.db.applications import ApplicationCreateData, ApplicationRepository
from excursion_ai.db.booking import BookingService
from excursion_ai.db.models import ApplicationStatus, CrmSyncStatus
from excursion_ai.db.session import dispose_engine, init_engine
from excursion_ai.db.sessions import (
    SessionCapacityError,
    SessionRepository,
    combine_rome,
    format_rome_time,
)
from excursion_ai.db.users import UserRepository
from excursion_ai.forms.validation import ROME_TZ, tomorrow_rome


def test_two_times_same_day_unique() -> None:
    day = date(2026, 11, 10)
    a = combine_rome(day, time(10, 0))
    b = combine_rome(day, time(15, 0))
    assert a.date() == b.date() or a.astimezone(ROME_TZ).date() == b.astimezone(ROME_TZ).date()
    assert format_rome_time(a) == "10:00"
    assert format_rome_time(b) == "15:00"
    assert a != b


def test_extract_webhook_events() -> None:
    payload = {
        "leads": {
            "status": [
                {"id": 11, "status_id": 22, "updated_at": 100},
                {"id": 12, "status_id": 33, "updated_at": 101},
            ]
        }
    }
    events = extract_lead_status_events(payload)
    assert len(events) == 2
    assert events[0][0] == "11"
    assert events[0][1] == 22


def test_booking_status_names_resolve() -> None:
    pipelines = [
        {
            "id": 1,
            "name": "ЭКСКУРСИИ",
            "_embedded": {
                "statuses": [
                    {"id": 10, "name": "Новая заявка"},
                    {"id": 20, "name": "Бронь подтверждена"},
                    {"id": 30, "name": "Бронь отменена"},
                ]
            },
        }
    ]
    ids = find_status_ids_by_names(
        pipelines,
        pipeline_name="ЭКСКУРСИИ",
        confirmed_name="Бронь подтверждена",
        cancelled_name="Бронь отменена",
    )
    assert ids == {"confirmed": 20, "cancelled": 30}
    try:
        find_status_ids_by_names(
            pipelines,
            pipeline_name="ЭКСКУРСИИ",
            confirmed_name="Нет такого",
            cancelled_name="Бронь отменена",
        )
        raise AssertionError("expected missing status")
    except AmoCrmApiError as error:
        assert error.code == "booking_status_confirmed_not_found"


async def _test_schedule_capacity_and_booking() -> None:
    settings = load_settings(require_db=True)
    factory = init_engine(settings)
    users = UserRepository(factory)
    apps = ApplicationRepository(factory)
    sessions = SessionRepository(factory)
    booking = BookingService(factory, sessions)
    tg = 9_200_000_000 + (uuid.uuid4().int % 1_000_000)
    day = tomorrow_rome() + timedelta(days=3)

    try:
        # Берём любую опубликованную экскурсию из каталога БД.
        from sqlalchemy import text

        async with factory() as db:
            excursion_id = await db.scalar(
                text(
                    "SELECT id FROM excursions WHERE status = 'published' ORDER BY id LIMIT 1"
                )
            )
        if not excursion_id:
            print("SKIP no published excursion")
            return

        await users.get_or_create(telegram_user_id=tg, language="ru")
        # Уникальный день, чтобы не пересекаться с прошлыми прогонами.
        day = tomorrow_rome() + timedelta(days=10 + (uuid.uuid4().int % 40))
        s10 = await sessions.create_session(
            excursion_id=str(excursion_id),
            starts_at=combine_rome(day, time(10, 0)),
            capacity=4,
        )
        s15 = await sessions.create_session(
            excursion_id=str(excursion_id),
            starts_at=combine_rome(day, time(15, 0)),
            capacity=2,
        )
        assert s10.id != s15.id

        available = await sessions.available_times_on_date(
            excursion_id=str(excursion_id),
            participants=3,
            day=day,
        )
        ids = {item.session.id for item in available}
        assert s10.id in ids
        assert s15.id not in ids  # вместимость 2 < 3

        alts = await sessions.list_available_for_participants(
            excursion_id=str(excursion_id),
            participants=3,
            limit=10,
        )
        assert any(item.session.id == s10.id for item in alts)

        app = await apps.create_application(
            telegram_user_id=tg,
            data=ApplicationCreateData(
                submission_key=f"sch-{uuid.uuid4().hex}",
                excursion_id=str(excursion_id),
                excursion_title="Schedule Test",
                language="ru",
                requested_date=day,
                session_id=s10.id,
                session_starts_at=s10.starts_at,
                participants_count=3,
                customer_name="Schedule Tester",
                unit_price=Decimal("20.00"),
                currency="EUR",
            ),
            crm_sync_status=CrmSyncStatus.NOT_CONNECTED.value,
        )
        app2 = await apps.create_application(
            telegram_user_id=tg,
            data=ApplicationCreateData(
                submission_key=f"sch2-{uuid.uuid4().hex}",
                excursion_id=str(excursion_id),
                excursion_title="Schedule Test",
                language="ru",
                requested_date=day,
                session_id=s10.id,
                session_starts_at=s10.starts_at,
                participants_count=2,
                customer_name="Schedule Tester 2",
                unit_price=Decimal("20.00"),
                currency="EUR",
            ),
            crm_sync_status=CrmSyncStatus.NOT_CONNECTED.value,
        )
        assert app.status == ApplicationStatus.NEW.value
        # Новая заявка места не занимает.
        assert await sessions.occupied_confirmed(s10.id) == 0

        confirmed = await booking.confirm_booking(app.id)
        assert confirmed.changed is True
        assert confirmed.conflict is False
        assert confirmed.application.status == ApplicationStatus.CONFIRMED.value
        assert await sessions.occupied_confirmed(s10.id) == 3

        # Повторное подтверждение идемпотентно.
        again = await booking.confirm_booking(app.id)
        assert again.changed is False
        assert await sessions.occupied_confirmed(s10.id) == 3

        # Вторая группа на 2 места — конфликт (осталось 1).
        conflict = await booking.confirm_booking(app2.id)
        assert conflict.conflict is True
        assert conflict.application.status == ApplicationStatus.NEW.value
        assert conflict.application.booking_conflict_code == "not_enough_seats"

        cancelled = await booking.cancel_booking(app.id)
        assert cancelled.changed is True
        assert cancelled.application.status == ApplicationStatus.CANCELLED.value
        assert await sessions.occupied_confirmed(s10.id) == 0

        # После отмены подтверждение второй заявки проходит.
        ok2 = await booking.confirm_booking(app2.id)
        assert ok2.conflict is False
        assert ok2.application.status == ApplicationStatus.CONFIRMED.value

        # Нельзя опустить вместимость ниже подтверждённых.
        try:
            await sessions.update_session(s10.id, capacity=1)
            raise AssertionError("expected capacity_below_confirmed")
        except SessionCapacityError as error:
            assert error.code == "capacity_below_confirmed"
    finally:
        await dispose_engine()


if __name__ == "__main__":
    test_two_times_same_day_unique()
    print("OK same-day times")
    test_extract_webhook_events()
    print("OK webhook extract")
    test_booking_status_names_resolve()
    print("OK booking status names")
    asyncio.run(_test_schedule_capacity_and_booking())
    print("OK schedule capacity + booking")
    print("OK schedule suite")
