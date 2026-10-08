"""Общие хелперы для DB-тестов с сеансами."""

from __future__ import annotations

import uuid
from datetime import time, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from excursion_ai.db.sessions import SessionRepository, combine_rome
from excursion_ai.forms.validation import tomorrow_rome
from excursion_ai.db.models import ExcursionSession


async def pick_published_excursion_id(
    factory: async_sessionmaker[AsyncSession],
) -> str:
    async with factory() as db:
        value = await db.scalar(
            text(
                "SELECT id FROM excursions WHERE status = 'published' ORDER BY id LIMIT 1"
            )
        )
    if not value:
        raise RuntimeError("no_published_excursion")
    return str(value)


async def create_test_session(
    factory: async_sessionmaker[AsyncSession],
    *,
    excursion_id: str,
    capacity: int = 20,
    hour: int = 10,
    minute: int = 0,
    days_ahead: int = 20,
) -> ExcursionSession:
    sessions = SessionRepository(factory)
    day = tomorrow_rome() + timedelta(days=days_ahead)
    # Уникальное время, чтобы не конфликтовать между прогонами.
    stamp = uuid.uuid4().int % 50
    hh = (hour + stamp // 60) % 23
    mm = (minute + stamp) % 60
    try:
        return await sessions.create_session(
            excursion_id=excursion_id,
            starts_at=combine_rome(day, time(hh, mm)),
            capacity=capacity,
        )
    except Exception:
        # Если дубликат — сдвинем на минуту.
        return await sessions.create_session(
            excursion_id=excursion_id,
            starts_at=combine_rome(day, time(hh, (mm + 1) % 60)),
            capacity=capacity,
        )
