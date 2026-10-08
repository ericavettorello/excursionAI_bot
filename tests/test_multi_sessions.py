"""Несколько сеансов и несколько времён в один день."""

from __future__ import annotations

import asyncio
import uuid
from datetime import time, timedelta

from excursion_ai.config import load_settings
from excursion_ai.db.session import dispose_engine, init_engine
from excursion_ai.db.sessions import (
    SessionRepository,
    SessionValidationError,
    combine_rome,
    format_rome_time,
    parse_rome_time,
)
from excursion_ai.forms.validation import ROME_TZ, tomorrow_rome
from excursion_ai.i18n import load_i18n
from sqlalchemy import text


async def _test_same_day_multi_times() -> None:
    settings = load_settings(require_db=True)
    factory = init_engine(settings)
    sessions = SessionRepository(factory)
    created: list = []
    try:
        async with factory() as db:
            excursion_id = await db.scalar(
                text(
                    "SELECT id FROM excursions WHERE status = 'published' "
                    "ORDER BY id LIMIT 1"
                )
            )
        assert excursion_id
        excursion_id = str(excursion_id)
        day = tomorrow_rome() + timedelta(days=60 + (uuid.uuid4().int % 80))

        s10 = await sessions.create_session(
            excursion_id=excursion_id,
            starts_at=combine_rome(day, time(10, 0)),
            capacity=12,
        )
        s15 = await sessions.create_session(
            excursion_id=excursion_id,
            starts_at=combine_rome(day, time(15, 0)),
            capacity=8,
        )
        created.extend([s10.id, s15.id])
        assert s10.id != s15.id
        assert format_rome_time(s10.starts_at) == "10:00"
        assert format_rome_time(s15.starts_at) == "15:00"

        # Дубликат того же времени запрещён.
        try:
            await sessions.create_session(
                excursion_id=excursion_id,
                starts_at=combine_rome(day, time(10, 0)),
                capacity=5,
            )
            raise AssertionError("expected duplicate")
        except SessionValidationError as error:
            assert error.code == "session_duplicate"

        # «Перезапуск»: новый репозиторий, полная выборка дня.
        sessions2 = SessionRepository(factory)
        day_slots = await sessions2.available_times_on_date(
            excursion_id=excursion_id,
            participants=1,
            day=day,
        )
        times = {format_rome_time(item.session.starts_at) for item in day_slots}
        assert "10:00" in times
        assert "15:00" in times
        assert len([x for x in day_slots if x.session.id in {s10.id, s15.id}]) == 2

        # Группа больше вместимости 15:00 (8) не видит этот слот, но видит 10:00.
        for_nine = await sessions2.available_times_on_date(
            excursion_id=excursion_id,
            participants=9,
            day=day,
        )
        ids_nine = {item.session.id for item in for_nine}
        assert s10.id in ids_nine
        assert s15.id not in ids_nine

        # Изменение и отключение.
        updated = await sessions2.update_session(s15.id, capacity=10)
        assert updated.capacity == 10
        await sessions2.deactivate_session(s15.id)
        assert (await sessions2.get_by_id(s15.id)).is_active is False

        # Валидация ввода.
        assert parse_rome_time("09:05").minute == 5
        try:
            parse_rome_time("99:99")
            raise AssertionError("bad time")
        except SessionValidationError:
            pass

        i18n = load_i18n()
        for lang in ("ru", "en", "it"):
            for key in (
                "admin.schedule.add",
                "admin.schedule.finish",
                "admin.schedule.wizard_hint",
                "admin.schedule.multi_hint",
                "admin.schedule.choose_action",
                "admin.btn.edit_data",
                "admin.btn.edit_schedule",
                "admin.edit_preview",
                "apply.ask_time_pick",
            ):
                assert i18n.t(lang, key)

        await sessions2.deactivate_session(s10.id)
        print(f"OK same-day multi times on {day.isoformat()} for {excursion_id}")
    finally:
        for session_id in created:
            try:
                await sessions.deactivate_session(session_id)
            except Exception:
                pass
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_test_same_day_multi_times())
    print("OK multi-sessions suite")
