"""Проверки, воспроизводящие причину молчания меню и работу навигации."""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime
from typing import Any

from aiogram.types import Chat, Message, User

from excursion_ai.ai.service import AIConsultantService
from excursion_ai.bot import AppContextMiddleware, create_dispatcher
from excursion_ai.catalog import load_catalog
from excursion_ai.config import SUPPORTED_LANGS, Settings
from excursion_ai.console import configure_console
from excursion_ai.handlers.common import MENU_KEYS, MenuText, all_menu_texts
from excursion_ai.i18n import load_i18n
from excursion_ai.languages import LanguageStore


def _disabled_ai(catalog) -> AIConsultantService:
    return AIConsultantService(
        Settings(bot_token="123456:TESTTOKEN_FOR_UNIT_TESTS_ONLY", ai_enabled=False),
        catalog=catalog,
    )


def _message(user_id: int, text: str) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Test"),
        text=text,
    )


def test_menu_texts_cover_all_languages() -> None:
    i18n = load_i18n()
    expected = {
        "ru": (
            "Экскурсии",
            "Помочь выбрать",
            "Мои заявки",
            "О сервисе",
            "Управление экскурсиями",
            "Язык",
        ),
        "en": (
            "Excursions",
            "Help me choose",
            "My applications",
            "About the service",
            "Manage excursions",
            "Language",
        ),
        "it": (
            "Escursioni",
            "Aiutami a scegliere",
            "Le mie richieste",
            "Sul servizio",
            "Gestione escursioni",
            "Lingua",
        ),
    }
    for lang, labels in expected.items():
        for key, label in zip(MENU_KEYS, labels, strict=True):
            assert i18n.t(lang, key) == label, f"{lang}/{key}"
    # 18 актуальных + 12 legacy-подписей
    assert len(all_menu_texts(i18n)) == 30


def test_menu_text_filter_requires_middleware_deps() -> None:
    """Регрессия: без langs/i18n в kwargs фильтр падает TypeError — меню молчит."""
    i18n = load_i18n()
    langs = LanguageStore()
    asyncio.run(langs.set(1, "en"))
    menu_filter = MenuText("menu.excursions")
    message = _message(1, "Excursions")

    signature = inspect.signature(menu_filter.__call__)
    assert "langs" in signature.parameters
    assert "i18n" in signature.parameters

    try:
        asyncio.run(menu_filter(message))
        raise AssertionError("Ожидался TypeError без зависимостей middleware")
    except TypeError as error:
        assert "langs" in str(error) or "i18n" in str(error) or "required" in str(error)

    assert asyncio.run(menu_filter(message, langs=langs, i18n=i18n)) is True
    assert asyncio.run(menu_filter(_message(1, "Экскурсии"), langs=langs, i18n=i18n)) is True


def test_menu_filter_accepts_stale_language_buttons() -> None:
    i18n = load_i18n()
    langs = LanguageStore()
    asyncio.run(langs.set(7, "en"))
    menu_filter = MenuText("menu.excursions")
    # Пользователь сменил язык на EN, но нажал старую RU-кнопку.
    assert asyncio.run(menu_filter(_message(7, "Экскурсии"), langs=langs, i18n=i18n)) is True
    assert asyncio.run(menu_filter(_message(7, "Escursioni"), langs=langs, i18n=i18n)) is True
    assert asyncio.run(menu_filter(_message(7, "Hello"), langs=langs, i18n=i18n)) is False


def test_language_store_is_per_user() -> None:
    async def scenario() -> None:
        langs = LanguageStore()
        await langs.set(101, "ru")
        await langs.set(202, "it")
        assert await langs.get(101) == "ru"
        assert await langs.get(202) == "it"
        assert await langs.get(303) is None

    asyncio.run(scenario())


async def _feed_text(
    dispatcher: Any,
    bot: Any,
    user_id: int,
    text: str,
    collected: list[dict[str, Any]],
) -> None:
    from aiogram.client.session.base import BaseSession
    from aiogram.methods import TelegramMethod
    from aiogram.types import Update

    class CollectSession(BaseSession):
        async def close(self) -> None:
            return None

        async def make_request(
            self,
            bot: Any,
            method: TelegramMethod[Any],
            timeout: int | None = None,
        ) -> Any:
            payload = method.model_dump(exclude_none=True)
            collected.append({"method": type(method).__name__, "payload": payload})
            # Минимальные ответы, чтобы aiogram не падал на типизации.
            if type(method).__name__ == "SendMessage":
                return Message(
                    message_id=len(collected),
                    date=datetime.now(),
                    chat=Chat(id=user_id, type="private"),
                    text=payload.get("text", ""),
                )
            return True

        async def stream_content(self, *args: Any, **kwargs: Any):  # noqa: ANN401
            yield b""

    bot.session = CollectSession()
    update = Update(
        update_id=user_id * 1000 + len(collected) + 1,
        message=_message(user_id, text),
    )
    await dispatcher.feed_update(bot, update)


def test_outer_middleware_makes_menu_handlers_respond() -> None:
    """С outer middleware пункты меню отвечают; раньше inner middleware ломал фильтры."""
    i18n = load_i18n()
    catalog = load_catalog()
    langs = LanguageStore()
    ai = _disabled_ai(catalog)
    dispatcher = create_dispatcher(i18n, catalog, langs, ai=ai)

    # Проверяем, что контекст именно outer.
    message_observer = dispatcher.observers["message"]
    assert any(isinstance(mw, AppContextMiddleware) for mw in message_observer.outer_middleware)
    assert not any(isinstance(mw, AppContextMiddleware) for mw in message_observer.middleware)

    from aiogram import Bot

    bot = Bot(token="123456:TESTTOKEN_FOR_UNIT_TESTS_ONLY")
    collected: list[dict[str, Any]] = []

    async def scenario() -> None:
        await _feed_text(dispatcher, bot, 11, "English", collected)
        await _feed_text(dispatcher, bot, 11, "Excursions", collected)
        await _feed_text(dispatcher, bot, 11, "About the service", collected)
        await _feed_text(dispatcher, bot, 11, "Language", collected)
        await _feed_text(dispatcher, bot, 11, "Italiano", collected)
        await _feed_text(dispatcher, bot, 11, "Escursioni", collected)
        await _feed_text(dispatcher, bot, 11, "Sul servizio", collected)
        await _feed_text(dispatcher, bot, 12, "Русский", collected)
        await _feed_text(dispatcher, bot, 12, "Экскурсии", collected)
        await _feed_text(dispatcher, bot, 11, "xyz-unknown", collected)

    asyncio.run(scenario())

    texts = [item["payload"].get("text", "") for item in collected if item["method"] == "SendMessage"]
    assert any("Welcome!" in text or "Benvenuto!" in text for text in texts)
    assert any(
        i18n.t("en", "catalog.title") in text
        or i18n.t("it", "catalog.title") in text
        or i18n.t("ru", "catalog.title") in text
        for text in texts
    )
    about_blob = "\n".join(texts).lower()
    assert any(
        token in about_blob
        for token in (
            "openai_api_key",
            "ai_enabled",
            "catalogue",
            "catalogo",
            "каталог",
            "request",
            "richiesta",
            "заявк",
            "excursion ai",
        )
    )
    assert any("Scegli la lingua" in text or "Choose a language" in text for text in texts)
    assert any("Excursion AI" in text for text in texts)
    assert any("Не понял запрос" in text or "did not understand" in text.lower() or "Non ho capito" in text for text in texts)

    # Языки пользователей независимы.
    assert asyncio.run(langs.get(11)) == "it"
    assert asyncio.run(langs.get(12)) == "ru"

    asyncio.run(bot.session.close())


def test_catalog_cards_for_all_languages() -> None:
    i18n = load_i18n()
    catalog = load_catalog()
    assert len(catalog.all()) == 4
    for excursion in catalog.all():
        for lang in SUPPORTED_LANGS:
            assert excursion.title(lang)
            assert excursion.price_per_person > 0
            assert i18n.t(lang, f"tour_language.{excursion.conducted_language}")


def main() -> None:
    configure_console()
    tests = [
        test_menu_texts_cover_all_languages,
        test_menu_text_filter_requires_middleware_deps,
        test_menu_filter_accepts_stale_language_buttons,
        test_language_store_is_per_user,
        test_catalog_cards_for_all_languages,
        test_outer_middleware_makes_menu_handlers_respond,
    ]
    for test in tests:
        test()
        print(f"OK {test.__name__}")
    print(f"Все проверки пройдены: {len(tests)}")


if __name__ == "__main__":
    main()
