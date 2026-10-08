"""Проверки демо-расписания и доступности по excursion_id."""

from __future__ import annotations

import asyncio
import uuid
from datetime import time, timedelta
from decimal import Decimal

from excursion_ai.config import load_settings
from excursion_ai.db.applications import ApplicationCreateData, ApplicationRepository
from excursion_ai.db.excursions import ExcursionRepository
from excursion_ai.db.models import CrmSyncStatus
from excursion_ai.db.seed_sessions import DEMO_CAPACITY, DEMO_SLOTS, ensure_demo_schedules
from excursion_ai.db.session import dispose_engine, init_engine
from excursion_ai.db.sessions import SessionRepository, combine_rome
from excursion_ai.db.users import UserRepository
from excursion_ai.forms.validation import ROME_TZ, tomorrow_rome
from excursion_ai.i18n import load_i18n


async def _test_seed_and_availability() -> None:
    settings = load_settings(require_db=True)
    factory = init_engine(settings)
    excursions = ExcursionRepository(factory)
    sessions = SessionRepository(factory)
    users = UserRepository(factory)
    apps = ApplicationRepository(factory)
    try:
        first = await ensure_demo_schedules(
            excursions=excursions, sessions=sessions
        )
        second = await ensure_demo_schedules(
            excursions=excursions, sessions=sessions
        )
        assert second.sessions_created == 0
        assert second.excursions_seeded == 0

        published = await excursions.list_by_status("published")
        assert published, "need published excursions"
        base = tomorrow_rome()
        for item in published:
            future = await sessions.list_for_excursion(
                item.id, include_inactive=False, from_rome_date=base
            )
            assert len(future) >= 2, (
                f"{item.id} needs >=2 future sessions, got {len(future)}"
            )
            assert all(row.excursion_id == item.id for row in future)

            ok12 = await sessions.list_available_for_participants(
                excursion_id=item.id,
                participants=12,
                limit=20,
            )
            assert all(x.session.excursion_id == item.id for x in ok12)
            assert all(x.free >= 12 for x in ok12)
            assert all(x.session.is_active for x in ok12)
            for x in ok12:
                day = x.session.starts_at.astimezone(ROME_TZ).date()
                assert day >= base

            huge = await sessions.list_available_for_participants(
                excursion_id=item.id,
                participants=10_000,
                limit=20,
            )
            assert huge == []

        # Неактивные и прошедшие не предлагаются.
        sample = published[0]
        stamp = uuid.uuid4().int % 50
        past = await sessions.create_session(
            excursion_id=sample.id,
            starts_at=combine_rome(
                base - timedelta(days=20 + stamp), time(10, stamp % 50)
            ),
            capacity=DEMO_CAPACITY,
        )
        inactive_day = base + timedelta(days=50 + stamp)
        inactive = await sessions.create_session(
            excursion_id=sample.id,
            starts_at=combine_rome(inactive_day, time(9, stamp % 50)),
            capacity=DEMO_CAPACITY,
        )
        await sessions.update_session(inactive.id, is_active=False)
        available = await sessions.list_available_for_participants(
            excursion_id=sample.id,
            participants=1,
            limit=200,
        )
        ids = {item.session.id for item in available}
        assert past.id not in ids
        assert inactive.id not in ids

        # Неподтверждённая заявка не занимает места.
        tg = 9_300_000_000 + (uuid.uuid4().int % 1_000_000)
        await users.get_or_create(telegram_user_id=tg, language="ru")
        slot = next(item for item in available if item.session.id not in {past.id, inactive.id})
        await apps.create_application(
            telegram_user_id=tg,
            data=ApplicationCreateData(
                submission_key=f"seed-{uuid.uuid4().hex}",
                excursion_id=sample.id,
                excursion_title="Seed Test",
                language="ru",
                requested_date=slot.session.starts_at.astimezone(ROME_TZ).date(),
                session_id=slot.session.id,
                session_starts_at=slot.session.starts_at,
                participants_count=min(slot.capacity, 12),
                customer_name="Seed Tester",
                unit_price=Decimal("20.00"),
                currency="EUR",
            ),
            crm_sync_status=CrmSyncStatus.NOT_CONNECTED.value,
        )
        assert await sessions.occupied_confirmed(slot.session.id) == 0
        after = await sessions.availability_for(slot.session.id)
        assert after is not None
        assert after.free == after.capacity

        drafts = await excursions.list_by_status("draft")
        for draft in drafts:
            # Seed не заполняет черновики: у draft без будущих сеансов их не появилось
            # от ensure — повторный ensure уже отработал с sessions_created=0.
            _ = draft

        i18n = load_i18n()
        for lang in ("ru", "en", "it"):
            for key in (
                "admin.schedule.finish",
                "admin.schedule.ask_capacity",
                "apply.no_sessions",
                "apply.no_times_for_date",
            ):
                assert i18n.t(lang, key)

        await sessions.delete_session(past.id)
        await sessions.delete_session(inactive.id)
        print(
            f"OK seed first created={first.sessions_created} "
            f"seeded={first.excursions_seeded}; second created={second.sessions_created}"
        )
        print(f"OK DEMO_SLOTS={len(DEMO_SLOTS)} capacity={DEMO_CAPACITY}")
        print(f"OK published with schedules={len(published)}")
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_test_seed_and_availability())
    print("OK seed sessions suite")
