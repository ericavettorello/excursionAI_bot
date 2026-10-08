from __future__ import annotations

from aiogram.types import CallbackQuery, Message

from excursion_ai.config import Settings
from excursion_ai.i18n import I18n
from excursion_ai.languages import LanguageStore


def is_private_chat(message_or_callback: Message | CallbackQuery) -> bool:
    if isinstance(message_or_callback, Message):
        chat = message_or_callback.chat
    else:
        message = message_or_callback.message
        if not isinstance(message, Message):
            return False
        chat = message.chat
    return chat.type == "private"


async def require_admin(
    message_or_callback: Message | CallbackQuery,
    settings: Settings,
    i18n: I18n,
    langs: LanguageStore,
) -> str | None:
    """Проверка доступа админа. Возвращает язык интерфейса или None при отказе."""
    user = message_or_callback.from_user
    if user is None or not is_private_chat(message_or_callback):
        await _deny(message_or_callback, i18n, lang="ru")
        return None

    lang = await langs.get(user.id) or "ru"
    if not settings.is_admin(user.id):
        await _deny(message_or_callback, i18n, lang=lang)
        return None
    return lang


async def _deny(
    message_or_callback: Message | CallbackQuery,
    i18n: I18n,
    *,
    lang: str,
) -> None:
    text = i18n.t(lang, "admin.denied")
    if isinstance(message_or_callback, CallbackQuery):
        await message_or_callback.answer()
        message = message_or_callback.message
        if isinstance(message, Message):
            await message.answer(text)
        return
    await message_or_callback.answer(text)
