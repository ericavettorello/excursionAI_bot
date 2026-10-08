"""Проверки текстов навигации без демо-фраз."""

from __future__ import annotations

from excursion_ai.ai.prompts import build_instructions
from excursion_ai.catalog import load_catalog_from_json
from excursion_ai.forms.texts import format_summary, status_label
from excursion_ai.handlers.common import LEGACY_MENU_TEXTS, MenuText, all_menu_texts
from excursion_ai.i18n import load_i18n
from excursion_ai.keyboards import (
    admin_main_keyboard,
    all_skip_phone_texts,
    excursion_card_keyboard,
    excursion_recommend_keyboard,
    main_menu,
)
from excursion_ai.texts import format_excursion_card
from decimal import Decimal
from datetime import date


FORBIDDEN_SNIPPETS = (
    "демонстрац",
    "вымышлен",
    "реальные бронирования не",
    "реальное бронирование не",
    "для демо",
    "demonstration",
    "fictional",
    "demo application",
    "real bookings are not",
    "Skip for demo",
    "dimostrativ",
    "fittiz",
    "Salta per demo",
)


def test_ui_strings_have_no_demo_phrases() -> None:
    i18n = load_i18n()
    keys = (
        "start.welcome",
        "about.text",
        "about.text_ai_ready",
        "ai.intro",
        "catalog.title",
        "catalog.demo_note",
        "apply.intro",
        "apply.ask_phone",
        "apply.skip_phone",
        "apply.summary_title",
        "apply.summary_demo",
        "apply.success",
        "my.title",
        "my.empty",
        "status.new",
        "menu.ask_ai",
        "menu.about",
        "menu.admin",
    )
    for lang in ("ru", "en", "it"):
        for key in keys:
            value = i18n.t(lang, key)
            lowered = value.lower()
            for snippet in FORBIDDEN_SNIPPETS:
                assert snippet.lower() not in lowered, f"{lang}/{key} contains {snippet!r}"


def test_status_accepted_not_booked() -> None:
    i18n = load_i18n()
    assert status_label("ru", i18n, "new") == "Принята"
    assert "брон" not in status_label("ru", i18n, "new").lower()


def test_card_and_summary_omit_empty_demo_notes() -> None:
    i18n = load_i18n()
    catalog = load_catalog_from_json()
    excursion = catalog.get("padua-intro")
    assert excursion is not None
    card = format_excursion_card(excursion, "ru", i18n)
    assert "демонстрац" not in card.lower()
    summary = format_summary(
        lang="ru",
        i18n=i18n,
        excursion_title="Test",
        requested_date=date(2026, 10, 10),
        participants_count=2,
        customer_name="Ivan",
        phone=None,
        unit_price=Decimal("10"),
        currency="EUR",
    )
    assert "демонстрац" not in summary.lower()


def test_legacy_menu_and_skip_phone_still_recognized() -> None:
    i18n = load_i18n()
    assert "Спросить ИИ" in all_menu_texts(i18n)
    assert "Пропустить для демо" in all_skip_phone_texts(i18n)
    assert "Пропустить" in all_skip_phone_texts(i18n)
    assert LEGACY_MENU_TEXTS["menu.ask_ai"]


def test_keyboards_use_new_labels() -> None:
    i18n = load_i18n()
    menu = main_menu("ru", i18n, is_admin=True)
    texts = [btn.text for row in menu.keyboard for btn in row]
    assert "Помочь выбрать" in texts
    assert "Управление экскурсиями" in texts
    assert "Язык" in texts
    admin = admin_main_keyboard("ru", i18n)
    admin_texts = [btn.text for row in admin.inline_keyboard for btn in row]
    assert admin_texts[0] == "Добавить экскурсию"
    assert "В клиентское меню" in admin_texts
    card = excursion_card_keyboard("ru", i18n, "padua-intro")
    card_texts = [btn.text for row in card.inline_keyboard for btn in row]
    assert card_texts == ["Оставить заявку", "Назад к экскурсиям", "Главное меню"]
    rec = excursion_recommend_keyboard("ru", i18n, "padua-intro")
    rec_texts = [btn.text for row in rec.inline_keyboard for btn in row]
    assert rec_texts[0] == "Открыть экскурсию"


def test_ai_instructions_avoid_demo_marketing() -> None:
    catalog = load_catalog_from_json()
    text = build_instructions(catalog, "ru").lower()
    assert "demonstration consultant" not in text
    assert "fictional walking" not in text
    assert "without a connected manager" not in text


if __name__ == "__main__":
    test_ui_strings_have_no_demo_phrases()
    test_status_accepted_not_booked()
    test_card_and_summary_omit_empty_demo_notes()
    test_legacy_menu_and_skip_phone_still_recognized()
    test_keyboards_use_new_labels()
    test_ai_instructions_avoid_demo_marketing()
    print("OK nav/copy checks")
