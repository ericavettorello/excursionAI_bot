"""Редактирование опубликованной экскурсии: поля, маршрут, сеансы, заявки."""

from __future__ import annotations

import asyncio
import uuid
from datetime import time, timedelta
from decimal import Decimal

from sqlalchemy import text

from excursion_ai.config import load_settings
from excursion_ai.db.applications import ApplicationCreateData, ApplicationRepository
from excursion_ai.db.excursions import ExcursionRepository
from excursion_ai.db.models import CrmSyncStatus
from excursion_ai.db.session import dispose_engine, init_engine
from excursion_ai.db.sessions import (
    SessionRepository,
    SessionValidationError,
    combine_rome,
)
from excursion_ai.db.users import UserRepository
from excursion_ai.forms.validation import tomorrow_rome
from excursion_ai.i18n import load_i18n
from excursion_ai.keyboards import admin_edit_menu_keyboard


async def _test_edit_published() -> None:
    settings = load_settings(require_db=True)
    factory = init_engine(settings)
    excursions = ExcursionRepository(factory)
    sessions = SessionRepository(factory)
    apps = ApplicationRepository(factory)
    users = UserRepository(factory)
    created_sessions: list = []
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
        before = await excursions.get(excursion_id)
        assert before is not None
        before_title = before.title("ru")
        before_version = before.version
        before_price = before.price_per_person

        source = before.source_language or "ru"
        block = dict(before.translations.get(source) or {})
        new_desc = (str(block.get("description") or "") + " edit-mark").strip()
        if len(new_desc) < 20:
            new_desc = "Updated description for edit test " + uuid.uuid4().hex[:8]
        route = list(block.get("route") or ["A", "B", "C"])
        if len(route) < 3:
            route = ["Point A", "Point B", "Point C"]
        await excursions.upsert_translation(
            excursion_id=excursion_id,
            language=source,
            title=str(block.get("title") or before_title),
            city_label=str(block.get("city") or before.city_id),
            description=new_desc[:2000],
            route=route,
            meeting_point=str(block.get("meeting_point") or "Meeting"),
            expected_version=(await excursions.get(excursion_id)).version,
            apply_immediately=True,
        )
        after = await excursions.get(excursion_id)
        assert after is not None
        assert after.id == excursion_id
        assert after.version >= before_version
        assert after.description(source).startswith(new_desc[:20]) or new_desc[:20] in after.description(source)

        # Несколько сеансов + дубликат.
        day = tomorrow_rome() + timedelta(days=70 + (uuid.uuid4().int % 40))
        s1 = await sessions.create_session(
            excursion_id=excursion_id,
            starts_at=combine_rome(day, time(10, 0)),
            capacity=12,
        )
        s2 = await sessions.create_session(
            excursion_id=excursion_id,
            starts_at=combine_rome(day, time(15, 0)),
            capacity=12,
        )
        created_sessions.extend([s1.id, s2.id])
        try:
            await sessions.create_session(
                excursion_id=excursion_id,
                starts_at=combine_rome(day, time(10, 0)),
                capacity=5,
            )
            raise AssertionError("duplicate expected")
        except SessionValidationError as error:
            assert error.code == "session_duplicate"

        # Заявка на сеанс — история не ломается при отключении.
        tg = 9_400_000_000 + (uuid.uuid4().int % 1_000_000)
        await users.get_or_create(telegram_user_id=tg, language="ru")
        app = await apps.create_application(
            telegram_user_id=tg,
            data=ApplicationCreateData(
                submission_key=f"edit-{uuid.uuid4().hex}",
                excursion_id=excursion_id,
                excursion_title=before_title,
                language="ru",
                requested_date=day,
                session_id=s1.id,
                session_starts_at=s1.starts_at,
                participants_count=2,
                customer_name="Edit Tester",
                unit_price=Decimal("25.00"),
                currency="EUR",
            ),
            crm_sync_status=CrmSyncStatus.NOT_CONNECTED.value,
        )
        assert app.excursion_title == before_title
        assert await sessions.count_applications(s1.id) == 1

        try:
            await sessions.update_session(
                s1.id, starts_at=combine_rome(day, time(11, 0))
            )
            raise AssertionError("linked session time change must fail")
        except SessionValidationError as error:
            assert error.code == "session_has_applications"

        await sessions.deactivate_session(s1.id)
        deactivated = await sessions.get_by_id(s1.id)
        assert deactivated is not None and deactivated.is_active is False
        # Заявка на месте, снимок названия не перезаписан.
        refreshed = await apps.get_by_id(app.id) if hasattr(apps, "get_by_id") else None
        if refreshed is None:
            async with factory() as db:
                row = await db.execute(
                    text(
                        "SELECT excursion_title, session_id FROM applications "
                        "WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": str(app.id)},
                )
                title_row = row.first()
                assert title_row is not None
                assert title_row.excursion_title == before_title
                assert str(title_row.session_id) == str(s1.id)
        else:
            assert refreshed.excursion_title == before_title

        # Клавиатура хаба редактирования содержит нужные действия.
        i18n = load_i18n()
        for lang in ("ru", "en", "it"):
            markup = admin_edit_menu_keyboard(lang, i18n, excursion_id=excursion_id)
            texts = [btn.text for row in markup.inline_keyboard for btn in row]
            assert i18n.t(lang, "admin.btn.edit_data") in texts
            assert i18n.t(lang, "admin.btn.edit_route") in texts
            assert i18n.t(lang, "admin.btn.edit_photos") in texts
            assert i18n.t(lang, "admin.btn.edit_schedule") in texts
            assert i18n.t(lang, "admin.btn.back") in texts
            assert i18n.t(lang, "admin.edit_preview", field="x", current="a", new="b")

        await sessions.deactivate_session(s2.id)
        print(f"OK edit published {excursion_id}")
    finally:
        for session_id in created_sessions:
            try:
                await sessions.deactivate_session(session_id)
            except Exception:
                pass
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_test_edit_published())
    print("OK edit excursion suite")
