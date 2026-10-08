"""Идемпотентное демо-расписание для опубликованных экскурсий без будущих сеансов."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta, time

from excursion_ai.db.excursions import ExcursionRepository
from excursion_ai.db.sessions import (
    SessionRepository,
    SessionValidationError,
    combine_rome,
)
from excursion_ai.forms.validation import tomorrow_rome
from excursion_ai.log_context import log_event

logger = logging.getLogger(__name__)

DEMO_CAPACITY = 12
# Не менее двух будущих сеансов: день+offset от tomorrow_rome, время Europe/Rome.
DEMO_SLOTS: tuple[tuple[int, time], ...] = (
    (3, time(10, 0)),
    (5, time(15, 0)),
)


@dataclass(frozen=True)
class DemoScheduleResult:
    excursions_checked: int
    excursions_seeded: int
    sessions_created: int
    sessions_skipped: int


async def ensure_demo_schedules(
    *,
    excursions: ExcursionRepository,
    sessions: SessionRepository,
) -> DemoScheduleResult:
    """Для каждой published-экскурсии без активных будущих сеансов создаёт демо-слоты.

    Не трогает черновики/архив. Не дублирует существующие (excursion_id, starts_at).
    Уже имеющие будущие сеансы пропускает целиком.
    """
    published = await excursions.list_by_status("published")
    created = 0
    skipped = 0
    seeded_excursions = 0
    base = tomorrow_rome()

    for item in published:
        future = await sessions.list_for_excursion(
            item.id,
            include_inactive=False,
            from_rome_date=base,
        )
        if future:
            continue

        seeded_excursions += 1
        for day_offset, hhmm in DEMO_SLOTS:
            starts = combine_rome(base + timedelta(days=day_offset), hhmm)
            try:
                await sessions.create_session(
                    excursion_id=item.id,
                    starts_at=starts,
                    capacity=DEMO_CAPACITY,
                    is_active=True,
                )
                created += 1
            except SessionValidationError as error:
                if error.code == "session_duplicate":
                    skipped += 1
                    continue
                raise

        log_event(
            logger,
            action="schedule.seed",
            stage="excursion",
            result="ok",
            excursion_id=item.id,
            created_slots=len(DEMO_SLOTS),
        )

    result = DemoScheduleResult(
        excursions_checked=len(published),
        excursions_seeded=seeded_excursions,
        sessions_created=created,
        sessions_skipped=skipped,
    )
    log_event(
        logger,
        action="schedule.seed",
        stage="done",
        result="ok",
        checked=result.excursions_checked,
        seeded=result.excursions_seeded,
        created=result.sessions_created,
        skipped=result.sessions_skipped,
    )
    return result
