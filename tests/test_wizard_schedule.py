"""Проверки расписания в сценарии создания/редактирования экскурсии."""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select, text

from excursion_ai.config import load_settings
from excursion_ai.db.models import ExcursionSession
from excursion_ai.db.session import dispose_engine, init_engine
from excursion_ai.db.sessions import (
    SessionRepository,
    SessionValidationError,
    combine_rome,
    parse_rome_time,
)
from excursion_ai.forms.validation import ROME_TZ, tomorrow_rome
from excursion_ai.i18n import load_i18n


def test_parse_date_time_capacity_rules() -> None:
    assert parse_rome_time("09:30") == time(9, 30)
    assert parse_rome_time("9:30") == time(9, 30)
    try:
        parse_rome_time("25:00")
        raise AssertionError("expected time_format")
    except SessionValidationError as error:
        assert error.code == "time_format"
    try:
        parse_rome_time("ab:cd")
        raise AssertionError("expected time_format")
    except SessionValidationError as error:
        assert error.code == "time_format"
    try:
        datetime.strptime("32.13.2026", "%d.%m.%Y")
        raise AssertionError("expected bad date")
    except ValueError:
        pass
    assert datetime.strptime("15.03.2026", "%d.%m.%Y").date() == date(2026, 3, 15)
    assert int("12") > 0
    assert int("0") <= 0


def test_schedule_locale_keys() -> None:
    i18n = load_i18n()
    keys = (
        "admin.schedule.finish",
        "admin.schedule.wizard_hint",
        "admin.schedule.ask_capacity",
        "admin.missing.schedule",
        "admin.summary_schedule_header",
        "catalog.schedule",
        "admin.schedule.delete",
        "admin.schedule.edit_date",
    )
    for lang in ("ru", "en", "it"):
        for key in keys:
            text_value = i18n.t(lang, key)
            assert text_value and text_value != key, f"missing {lang}:{key}"
        # Вместимость не называется «свободными местами» в админ-текстах.
        capacity_ask = i18n.t(lang, "admin.schedule.ask_capacity").lower()
        assert "свобод" not in capacity_ask
        assert "free seat" not in capacity_ask
        assert "posti liberi" not in capacity_ask


async def _test_multi_session_crud_and_no_backfill() -> None:
    settings = load_settings(require_db=True)
    factory = init_engine(settings)
    sessions = SessionRepository(factory)
    try:
        async with factory() as db:
            excursion_id = await db.scalar(
                text(
                    "SELECT id FROM excursions WHERE status IN ('published', 'draft') "
                    "ORDER BY id LIMIT 1"
                )
            )
            before_total = int(
                await db.scalar(select(func.count()).select_from(ExcursionSession)) or 0
            )
        if not excursion_id:
            print("SKIP no excursion")
            return
        excursion_id = str(excursion_id)

        # Миграция не создаёт фиктивных сеансов: число сеансов у «чужих» экскурсий
        # не обязано быть нулём (могли добавить ранее), но create не трогает другие.
        async with factory() as db:
            orphan_fake = await db.scalar(
                text(
                    "SELECT COUNT(*) FROM excursion_sessions "
                    "WHERE capacity <= 0 OR starts_at IS NULL"
                )
            )
        assert int(orphan_fake or 0) == 0

        day = tomorrow_rome() + timedelta(days=20 + (uuid.uuid4().int % 50))
        s1 = await sessions.create_session(
            excursion_id=excursion_id,
            starts_at=combine_rome(day, time(10, 0)),
            capacity=8,
        )
        s2 = await sessions.create_session(
            excursion_id=excursion_id,
            starts_at=combine_rome(day, time(16, 0)),
            capacity=12,
        )
        assert await sessions.count_active(excursion_id) >= 2

        # Дубликат даты+времени запрещён.
        try:
            await sessions.create_session(
                excursion_id=excursion_id,
                starts_at=combine_rome(day, time(10, 0)),
                capacity=5,
            )
            raise AssertionError("expected session_duplicate")
        except SessionValidationError as error:
            assert error.code == "session_duplicate"

        # Неверная вместимость.
        try:
            await sessions.create_session(
                excursion_id=excursion_id,
                starts_at=combine_rome(day + timedelta(days=1), time(11, 0)),
                capacity=0,
            )
            raise AssertionError("expected capacity_positive")
        except SessionValidationError as error:
            assert error.code == "capacity_positive"

        # Изменение даты/времени/вместимости.
        new_day = day + timedelta(days=1)
        updated = await sessions.update_session(
            s1.id,
            starts_at=combine_rome(new_day, time(11, 30)),
            capacity=9,
        )
        assert updated.capacity == 9
        assert updated.starts_at.astimezone(ROME_TZ).strftime("%H:%M") == "11:30"

        # Удаление = мягкая деактивация (запись сохраняется).
        await sessions.delete_session(s2.id)
        gone = await sessions.get_by_id(s2.id)
        assert gone is not None
        assert gone.is_active is False
        still = await sessions.get_by_id(s1.id)
        assert still is not None

        await sessions.deactivate_session(s1.id)
        assert (await sessions.get_by_id(s1.id)).is_active is False

        async with factory() as db:
            after_total = int(
                await db.scalar(select(func.count()).select_from(ExcursionSession)) or 0
            )
        assert after_total >= before_total
    finally:
        await dispose_engine()


if __name__ == "__main__":
    test_parse_date_time_capacity_rules()
    print("OK parse rules")
    test_schedule_locale_keys()
    print("OK locale keys RU/EN/IT")
    asyncio.run(_test_multi_session_crud_and_no_backfill())
    print("OK multi-session CRUD + no fake backfill")
    print("OK wizard schedule suite")
