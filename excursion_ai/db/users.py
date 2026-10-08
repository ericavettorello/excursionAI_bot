from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from excursion_ai.config import SUPPORTED_LANGS
from excursion_ai.db.models import User
from excursion_ai.log_context import log_event

logger = logging.getLogger(__name__)


class UserRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_by_telegram_id(self, telegram_user_id: int) -> User | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(User).where(User.telegram_user_id == telegram_user_id)
            )

    async def get_or_create(self, telegram_user_id: int, language: str | None = None) -> User:
        if language is not None and language not in SUPPORTED_LANGS:
            raise ValueError("unsupported_language")

        async with self._session_factory() as session:
            async with session.begin():
                existing = await session.scalar(
                    select(User).where(User.telegram_user_id == telegram_user_id)
                )
                if existing is not None:
                    log_event(
                        logger,
                        action="user.get_or_create",
                        stage="existing",
                        result="ok",
                    )
                    return existing

                stmt = (
                    insert(User)
                    .values(telegram_user_id=telegram_user_id, language=language)
                    .on_conflict_do_nothing(index_elements=[User.telegram_user_id])
                    .returning(User.id)
                )
                inserted_id = await session.scalar(stmt)
                if inserted_id is None:
                    user = await session.scalar(
                        select(User).where(User.telegram_user_id == telegram_user_id)
                    )
                    if user is None:
                        raise RuntimeError("user_create_failed")
                    log_event(
                        logger,
                        action="user.get_or_create",
                        stage="race_resolved",
                        result="ok",
                    )
                    return user

                user = await session.get(User, inserted_id)
                if user is None:
                    raise RuntimeError("user_create_failed")
                log_event(
                    logger,
                    action="user.get_or_create",
                    stage="created",
                    result="ok",
                )
                return user

    async def set_language(self, telegram_user_id: int, language: str) -> User:
        if language not in SUPPORTED_LANGS:
            raise ValueError("unsupported_language")
        now = datetime.now(timezone.utc)

        async with self._session_factory() as session:
            async with session.begin():
                user = await session.scalar(
                    select(User).where(User.telegram_user_id == telegram_user_id)
                )
                if user is None:
                    stmt = (
                        insert(User)
                        .values(
                            telegram_user_id=telegram_user_id,
                            language=language,
                            created_at=now,
                            updated_at=now,
                        )
                        .on_conflict_do_update(
                            index_elements=[User.telegram_user_id],
                            set_={"language": language, "updated_at": now},
                        )
                        .returning(User.id)
                    )
                    user_id = await session.scalar(stmt)
                    user = await session.get(User, user_id)
                else:
                    user.language = language
                    user.updated_at = now
                    await session.flush()

                if user is None:
                    raise RuntimeError("language_update_failed")
                log_event(
                    logger,
                    action="user.set_language",
                    stage="save",
                    result="ok",
                    lang=language,
                )
                return user
