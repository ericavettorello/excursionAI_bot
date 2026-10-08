from __future__ import annotations

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import BigInteger, Date, Integer, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from excursion_ai.ai.errors import AILimitError
from excursion_ai.db.base import Base
from excursion_ai.log_context import log_event

logger = logging.getLogger(__name__)
ROME_TZ = ZoneInfo("Europe/Rome")


def today_rome(now: datetime | None = None) -> date:
    current = now or datetime.now(ROME_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=ROME_TZ)
    return current.astimezone(ROME_TZ).date()


class AIDailyUserUsage(Base):
    __tablename__ = "ai_daily_user_usage"

    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AIDailyGlobalUsage(Base):
    __tablename__ = "ai_daily_global_usage"

    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AIDailyAdminTranslationUsage(Base):
    __tablename__ = "ai_daily_admin_translation_usage"

    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AIUsageRepository:
    """Атомарное резервирование дневных лимитов (день — Europe/Rome)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def try_reserve(
        self,
        *,
        telegram_user_id: int,
        user_limit: int,
        global_limit: int,
        usage_day: date | None = None,
    ) -> tuple[int, int]:
        day = usage_day or today_rome()
        async with self._session_factory() as session:
            async with session.begin():
                user_stmt = (
                    insert(AIDailyUserUsage)
                    .values(
                        usage_date=day,
                        telegram_user_id=telegram_user_id,
                        request_count=1,
                    )
                    .on_conflict_do_update(
                        index_elements=[
                            AIDailyUserUsage.usage_date,
                            AIDailyUserUsage.telegram_user_id,
                        ],
                        set_={"request_count": AIDailyUserUsage.request_count + 1},
                        where=AIDailyUserUsage.request_count < user_limit,
                    )
                    .returning(AIDailyUserUsage.request_count)
                )
                user_count = await session.scalar(user_stmt)
                if user_count is None:
                    log_event(
                        logger,
                        action="ai.limit",
                        stage="user",
                        result="exceeded",
                    )
                    raise AILimitError("user_limit")

                global_stmt = (
                    insert(AIDailyGlobalUsage)
                    .values(usage_date=day, request_count=1)
                    .on_conflict_do_update(
                        index_elements=[AIDailyGlobalUsage.usage_date],
                        set_={"request_count": AIDailyGlobalUsage.request_count + 1},
                        where=AIDailyGlobalUsage.request_count < global_limit,
                    )
                    .returning(AIDailyGlobalUsage.request_count)
                )
                global_count = await session.scalar(global_stmt)
                if global_count is None:
                    log_event(
                        logger,
                        action="ai.limit",
                        stage="global",
                        result="exceeded",
                    )
                    # Откат транзакции снимет и user increment.
                    raise AILimitError("global_limit")

                log_event(
                    logger,
                    action="ai.limit",
                    stage="reserve",
                    result="ok",
                    user_count=user_count,
                    global_count=global_count,
                )
                return int(user_count), int(global_count)

    async def try_reserve_admin_translation(
        self,
        *,
        telegram_user_id: int,
        daily_limit: int,
        usage_day: date | None = None,
    ) -> int:
        """Отдельный лимит админ-переводов (не трогает клиентскую квоту)."""
        day = usage_day or today_rome()
        async with self._session_factory() as session:
            async with session.begin():
                stmt = (
                    insert(AIDailyAdminTranslationUsage)
                    .values(
                        usage_date=day,
                        telegram_user_id=telegram_user_id,
                        request_count=1,
                    )
                    .on_conflict_do_update(
                        index_elements=[
                            AIDailyAdminTranslationUsage.usage_date,
                            AIDailyAdminTranslationUsage.telegram_user_id,
                        ],
                        set_={
                            "request_count": AIDailyAdminTranslationUsage.request_count
                            + 1
                        },
                        where=AIDailyAdminTranslationUsage.request_count < daily_limit,
                    )
                    .returning(AIDailyAdminTranslationUsage.request_count)
                )
                count = await session.scalar(stmt)
                if count is None:
                    log_event(
                        logger,
                        action="ai.limit",
                        stage="admin_translation",
                        result="exceeded",
                    )
                    raise AILimitError("admin_translation_limit")
                log_event(
                    logger,
                    action="ai.limit",
                    stage="admin_translation_reserve",
                    result="ok",
                    user_count=count,
                )
                return int(count)

    async def get_counts(
        self,
        *,
        telegram_user_id: int,
        usage_day: date | None = None,
    ) -> tuple[int, int]:
        day = usage_day or today_rome()
        async with self._session_factory() as session:
            user_count = await session.scalar(
                select(AIDailyUserUsage.request_count).where(
                    AIDailyUserUsage.usage_date == day,
                    AIDailyUserUsage.telegram_user_id == telegram_user_id,
                )
            )
            global_count = await session.scalar(
                select(AIDailyGlobalUsage.request_count).where(
                    AIDailyGlobalUsage.usage_date == day
                )
            )
            return int(user_count or 0), int(global_count or 0)
