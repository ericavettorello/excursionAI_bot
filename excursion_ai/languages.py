from __future__ import annotations

import logging

from excursion_ai.config import SUPPORTED_LANGS
from excursion_ai.db.users import UserRepository
from excursion_ai.log_context import log_event

logger = logging.getLogger(__name__)


class LanguageStore:
    """Язык интерфейса: кэш в памяти + сохранение в PostgreSQL.

    Без репозитория работает только в памяти (для unit-тестов без БД).
    """

    def __init__(self, users: UserRepository | None = None) -> None:
        self._users = users
        self._cache: dict[int, str] = {}

    async def get(self, telegram_user_id: int) -> str | None:
        cached = self._cache.get(telegram_user_id)
        if cached is not None:
            return cached
        if self._users is None:
            return None
        user = await self._users.get_by_telegram_id(telegram_user_id)
        if user is None or user.language is None:
            return None
        if user.language not in SUPPORTED_LANGS:
            return None
        self._cache[telegram_user_id] = user.language
        return user.language

    async def set(self, telegram_user_id: int, lang: str) -> None:
        if lang not in SUPPORTED_LANGS:
            raise ValueError("unsupported_language")
        if self._users is not None:
            await self._users.set_language(telegram_user_id, lang)
            log_event(
                logger,
                action="language.persist",
                stage="db",
                result="ok",
                lang=lang,
            )
        self._cache[telegram_user_id] = lang
