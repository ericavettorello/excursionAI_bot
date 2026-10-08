"""UX заявки, фильтр будущих сеансов и лимит callback_data админ-расписания."""

from __future__ import annotations

import asyncio
import inspect
import uuid
from datetime import time, timedelta
from unittest.mock import AsyncMock, MagicMock

from excursion_ai.config import load_settings
from excursion_ai.db.session import dispose_engine, init_engine
from excursion_ai.db.sessions import (
    SessionRepository,
    combine_rome,
    is_upcoming_rome,
)
from excursion_ai.forms.validation import ROME_TZ, now_rome, tomorrow_rome
from excursion_ai.handlers import apply_flow
from excursion_ai.i18n import load_i18n
from excursion_ai.keyboards import (
    AdminScheduleCallback,
    admin_schedule_error_keyboard,
    admin_schedule_list_keyboard,
    admin_schedule_nav_keyboard,
    phone_keyboard,
)


def test_apply_greeting_and_questions_localized() -> None:
    i18n = load_i18n()
    for lang in ("ru", "en", "it"):
        greeting = i18n.t(lang, "apply.greeting", title="Demo")
        assert "{title}" not in greeting
        assert "Demo" in greeting
        assert i18n.t(lang, "apply.ask_participants")
        assert i18n.t(lang, "apply.ask_name")
        assert i18n.t(lang, "apply.ask_phone")
        assert i18n.t(lang, "apply.skip_phone")
        assert i18n.t(lang, "admin.schedule.tech_error", error_id="abc123")
        assert i18n.t(lang, "admin.schedule.retry")
        assert i18n.t(lang, "admin.schedule.nav_hint")
        assert i18n.t(lang, "admin.schedule.page", page=1, pages=2)
    print("OK apply/admin locales")


def test_start_application_sends_greeting_then_participants() -> None:
    i18n = load_i18n()
    answers: list[str] = []

    class FakeExcursion:
        id = "demo-tour"
        status = "published"
        version = 1
        price_per_person = 10
        currency = "EUR"

        def title(self, lang: str) -> str:
            return "Demo Tour"

    catalog = MagicMock()
    catalog.get.return_value = FakeExcursion()

    message = AsyncMock()
    message.answer = AsyncMock(side_effect=lambda text, **kwargs: answers.append(text))
    state = AsyncMock()
    state.set_state = AsyncMock()
    state.set_data = AsyncMock()

    asyncio.run(
        apply_flow.start_application_form(
            message,
            state=state,
            lang="ru",
            i18n=i18n,
            catalog=catalog,
            excursion_id="demo-tour",
        )
    )
    assert len(answers) >= 2
    assert "Demo Tour" in answers[0]
    assert "участник" in answers[1].lower()
    assert answers[0] != answers[1]
    source = inspect.getsource(apply_flow.start_application_form)
    assert "apply.greeting" in source
    assert "apply.ask_participants" in source
    print("OK apply greeting then participants")


def test_admin_schedule_callbacks_under_64_bytes() -> None:
    sid = "a0fbec9e-1516-4d83-a92d-4109244c36e7"
    eid = "florence-renaissance"
    for packed in (
        AdminScheduleCallback(action="view", session_id=sid).pack(),
        AdminScheduleCallback(action="edit_date", session_id=sid).pack(),
        AdminScheduleCallback(action="edit_time", session_id=sid).pack(),
        AdminScheduleCallback(action="edit_capacity", session_id=sid).pack(),
        AdminScheduleCallback(action="delete", session_id=sid).pack(),
        AdminScheduleCallback(action="toggle", session_id=sid).pack(),
        AdminScheduleCallback(action="deactivate_confirm", session_id=sid).pack(),
        AdminScheduleCallback(action="add", excursion_id=eid).pack(),
        AdminScheduleCallback(action="excursion", excursion_id=eid).pack(),
        AdminScheduleCallback(action="nav_back").pack(),
        AdminScheduleCallback(action="nav_cancel").pack(),
        AdminScheduleCallback(action="retry").pack(),
        AdminScheduleCallback(action="page_next").pack(),
        AdminScheduleCallback(action="page_prev").pack(),
    ):
        assert len(packed.encode()) <= 64, packed

    view = AdminScheduleCallback(action="view", session_id=sid).pack()
    assert eid not in view
    # Регрессия 581e91a9: старый pack с excursion_id+session_id был > 64.
    legacy = f"asch:view:{eid}:{sid}:0:"
    assert len(legacy.encode()) > 64
    assert len(view.encode()) <= 64
    print("OK admin schedule callback length")


def test_admin_schedule_nav_and_error_keyboards() -> None:
    i18n = load_i18n()
    for lang in ("ru", "en", "it"):
        nav = admin_schedule_nav_keyboard(lang, i18n)
        err = admin_schedule_error_keyboard(lang, i18n)
        list_kb = admin_schedule_list_keyboard(
            lang,
            i18n,
            excursion_id="florence-renaissance",
            sessions=[
                (
                    "a0fbec9e-1516-4d83-a92d-4109244c36e7",
                    "01.11.2026 10:00 · 12",
                    True,
                )
            ],
            mode="card",
            page=0,
            pages=2,
        )
        phone = phone_keyboard(lang, i18n)
        assert nav.inline_keyboard
        assert len(err.inline_keyboard) >= 3
        assert list_kb.inline_keyboard
        assert phone.keyboard
        assert i18n.t(lang, "apply.skip_phone") in {
            row[0].text for row in phone.keyboard
        }
    print("OK nav/error/phone keyboards")


async def _test_upcoming_filter() -> None:
    settings = load_settings(require_db=True)
    factory = init_engine(settings)
    sessions = SessionRepository(factory)
    created: list[uuid.UUID] = []
    try:
        from sqlalchemy import text

        async with factory() as db:
            excursion_id = await db.scalar(
                text(
                    "SELECT id FROM excursions WHERE status = 'published' "
                    "ORDER BY id LIMIT 1"
                )
            )
        assert excursion_id
        excursion_id = str(excursion_id)
        moment = now_rome()
        stamp = uuid.uuid4().int % 40
        day_future = tomorrow_rome() + timedelta(days=40 + stamp)
        day_past = (moment - timedelta(days=3 + (stamp % 5))).date()
        past_minute = 1 + (stamp % 50)
        today_offset_minutes = 30 + (stamp % 80)

        future = await sessions.create_session(
            excursion_id=excursion_id,
            starts_at=combine_rome(day_future, time(11, past_minute % 60)),
            capacity=10,
        )
        created.append(future.id)

        past = await sessions.create_session(
            excursion_id=excursion_id,
            starts_at=combine_rome(day_past, time(9, past_minute % 60)),
            capacity=10,
        )
        created.append(past.id)

        today_past_at = moment - timedelta(minutes=today_offset_minutes)
        if today_past_at.tzinfo is None:
            today_past_at = today_past_at.replace(tzinfo=ROME_TZ)
        # Уникальная секунда, чтобы не столрить с прошлыми прогонами теста.
        today_past_at = today_past_at.replace(second=stamp % 60, microsecond=stamp)
        today_row = await sessions.create_session(
            excursion_id=excursion_id,
            starts_at=today_past_at,
            capacity=10,
        )
        created.append(today_row.id)

        inactive = await sessions.create_session(
            excursion_id=excursion_id,
            starts_at=combine_rome(day_future, time(16, past_minute % 60)),
            capacity=10,
            is_active=False,
        )
        created.append(inactive.id)

        upcoming = await sessions.list_upcoming_active(excursion_id, now=moment)
        ids = {row.id for row in upcoming}
        assert future.id in ids
        assert past.id not in ids
        assert today_row.id not in ids
        assert inactive.id not in ids

        available = await sessions.list_available_for_participants(
            excursion_id=excursion_id,
            participants=1,
            now=moment,
        )
        avail_ids = {item.session.id for item in available}
        assert future.id in avail_ids
        assert past.id not in avail_ids
        assert today_row.id not in avail_ids
        assert inactive.id not in avail_ids
        assert is_upcoming_rome(future.starts_at, now=moment)
        assert not is_upcoming_rome(past.starts_at, now=moment)
        assert not is_upcoming_rome(today_past_at, now=moment)
        print(f"OK upcoming filter for {excursion_id}")
    finally:
        for session_id in created:
            try:
                await sessions.deactivate_session(session_id)
            except Exception:
                pass
        await dispose_engine()


def test_upcoming_filter() -> None:
    asyncio.run(_test_upcoming_filter())


if __name__ == "__main__":
    test_apply_greeting_and_questions_localized()
    test_start_application_sends_greeting_then_participants()
    test_admin_schedule_callbacks_under_64_bytes()
    test_admin_schedule_nav_and_error_keyboards()
    test_upcoming_filter()
    print("OK apply-schedule UX suite")
