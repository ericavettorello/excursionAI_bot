from __future__ import annotations

from aiogram.filters import Filter
from aiogram.types import Message

from excursion_ai.config import SUPPORTED_LANGS
from excursion_ai.i18n import I18n
from excursion_ai.languages import LanguageStore

LANGUAGE_CODES = {
    "Русский": "ru",
    "English": "en",
    "Italiano": "it",
}

MENU_KEYS = (
    "menu.excursions",
    "menu.ask_ai",
    "menu.my_applications",
    "menu.about",
    "menu.admin",
    "menu.change_language",
)

# Старые подписи reply-кнопок после смены формулировок (история чата).
LEGACY_MENU_TEXTS: dict[str, frozenset[str]] = {
    "menu.ask_ai": frozenset(
        {
            "Спросить ИИ",
            "Ask AI",
            "Chiedi all'IA",
        }
    ),
    "menu.about": frozenset(
        {
            "О проекте",
            "About the project",
            "Informazioni sul progetto",
        }
    ),
    "menu.admin": frozenset(
        {
            "Админ",
            "Admin",
            "Amministrazione",
        }
    ),
    "menu.change_language": frozenset(
        {
            "Сменить язык",
            "Change language",
            "Cambia lingua",
        }
    ),
}

LEGACY_SKIP_PHONE = frozenset(
    {
        "Пропустить для демо",
        "Skip for demo",
        "Salta per demo",
    }
)

LEGACY_CANCEL = frozenset(
    {
        "Отмена",
        "Cancel",
        "Annulla",
    }
)


class MenuText(Filter):
    """Сопоставляет текст reply-кнопки меню на любом из языков интерфейса.

    Сопоставление по всем языкам нужно для старых кнопок после смены языка.
    Фильтру нужны langs и i18n из outer middleware — иначе будет TypeError.
    """

    def __init__(self, key: str) -> None:
        self.key = key

    async def __call__(
        self,
        message: Message,
        langs: LanguageStore,
        i18n: I18n,
    ) -> bool:
        if not message.from_user or not message.text:
            return False
        expected = {i18n.t(lang, self.key) for lang in SUPPORTED_LANGS}
        expected |= set(LEGACY_MENU_TEXTS.get(self.key, ()))
        return message.text in expected


async def current_lang(message: Message, langs: LanguageStore) -> str | None:
    if not message.from_user:
        return None
    return await langs.get(message.from_user.id)


def all_menu_texts(i18n: I18n) -> set[str]:
    texts: set[str] = set()
    for lang in SUPPORTED_LANGS:
        for key in MENU_KEYS:
            texts.add(i18n.t(lang, key))
    for aliases in LEGACY_MENU_TEXTS.values():
        texts.update(aliases)
    return texts


class CancelText(Filter):
    """Кнопка «Отмена» / «Отменить оформление» на любом языке интерфейса."""

    async def __call__(self, message: Message, i18n: I18n) -> bool:
        if not message.text:
            return False
        expected = {i18n.t(lang, "apply.cancel") for lang in SUPPORTED_LANGS}
        expected |= set(LEGACY_CANCEL)
        return message.text in expected
