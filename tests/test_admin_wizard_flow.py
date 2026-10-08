"""FSM-шаги текста, шаблоны admin.ask_* и итог после исходного языка."""

from __future__ import annotations

from decimal import Decimal
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from excursion_ai.catalog import Excursion
from excursion_ai.config import SUPPORTED_LANGS
from excursion_ai.handlers.admin import (
    TEXT_WIZARD_STEPS,
    _advance_text_wizard,
    _first_open_text_step,
    _format_admin_text_field_prompt,
    _merged_lang_text_block,
    _next_text_step,
    _send_summary_after_source,
    _text_step_satisfied,
)
from excursion_ai.i18n import load_i18n, template_field_names


def _draft(**kwargs: object) -> Excursion:
    base = {
        "id": "rome-test",
        "city_id": "rome",
        "duration_minutes": 60,
        "price_per_person": Decimal("10"),
        "currency": "EUR",
        "conducted_languages": ("en", "it"),
        "translations": {},
        "status": "draft",
        "version": 1,
        "source_language": "ru",
    }
    base.update(kwargs)
    return Excursion(**base)  # type: ignore[arg-type]


def test_text_wizard_step_order_and_skip_filled() -> None:
    excursion = _draft()
    buffer = {
        "ru": {
            "title": "Название экскурсии",
            "description": "Описание достаточно длинное для проверки",
        }
    }
    assert _first_open_text_step(excursion, "ru", buffer) == "route"
    assert _next_text_step("title") == "description"
    assert _next_text_step("meeting") is None
    merged = _merged_lang_text_block(excursion, "ru", buffer)
    assert _text_step_satisfied(merged, "title") is True
    assert _text_step_satisfied(merged, "description") is True
    assert _text_step_satisfied(merged, "route") is False


def test_admin_ask_templates_format_all_langs() -> None:
    i18n = load_i18n()
    keys = (
        "admin.ask_title",
        "admin.ask_description",
        "admin.ask_route",
        "admin.ask_meeting",
    )
    params = {
        "title": "Sample tour",
        "lang_block": "RU block",
    }
    for lang in SUPPORTED_LANGS:
        for key in keys:
            fields = template_field_names(i18n.t(lang, key))
            assert fields <= {"title", "lang_block", "code"}
            text = i18n.t(lang, key, **params)
            assert "Sample tour" in text
            assert "{" not in text


def test_format_admin_text_field_prompt_route() -> None:
    i18n = load_i18n()
    prompt = _format_admin_text_field_prompt(
        "ru",
        i18n,
        "route",
        title_hint="Рим",
        target_code="en",
    )
    assert "Рим" in prompt
    assert "English" in prompt or "Английская" in prompt or "inglese" in prompt.lower()


async def _test_advance_after_title_skips_to_route_when_description_in_buffer() -> None:
    i18n = load_i18n()
    excursion = _draft()
    state = AsyncMock()
    state.get_data = AsyncMock(
        return_value={
            "excursion_id": excursion.id,
            "active_lang": "ru",
            "source_lang": "ru",
            "draft_buffer": {
                "ru": {
                    "title": "Название экскурсии",
                    "description": "Описание достаточно длинное для проверки",
                }
            },
        }
    )
    state.set_state = AsyncMock()
    state.set_data = AsyncMock()
    message = AsyncMock()
    excursions = AsyncMock()
    excursions.get = AsyncMock(return_value=excursion)
    catalog = MagicMock()

    with patch(
        "excursion_ai.handlers.admin._prompt_translation_step",
        new_callable=AsyncMock,
    ) as prompt:
        await _advance_text_wizard(
            message,
            state=state,
            lang="ru",
            i18n=i18n,
            excursions=excursions,
            catalog_store=catalog,
            completed_step="title",
        )
        prompt.assert_awaited_once()
        assert prompt.await_args.kwargs["step"] == "route"


async def _test_summary_after_source_offers_translate_button() -> None:
    i18n = load_i18n()
    block = {
        "title": "Title ok",
        "city": "rome",
        "description": "Description long enough",
        "route": ["a", "b", "c"],
        "meeting_point": "Meet point",
    }
    excursion = _draft(translations={"ru": block}, source_language="ru")
    state = AsyncMock()
    state.set_state = AsyncMock()
    message = AsyncMock()
    excursions = AsyncMock()
    excursions.get = AsyncMock(return_value=excursion)

    with patch(
        "excursion_ai.handlers.admin._send_summary",
        new_callable=AsyncMock,
    ) as summary:
        await _send_summary_after_source(
            message,
            state=state,
            lang="ru",
            i18n=i18n,
            excursions=excursions,
            excursion_id=excursion.id,
            notice="saved",
        )
        summary.assert_awaited_once()
        assert summary.await_args.kwargs["show_translate_en_it"] is True


def test_wizard_steps_tuple_matches_handlers() -> None:
    assert TEXT_WIZARD_STEPS == ("title", "description", "route", "meeting")


if __name__ == "__main__":
    test_text_wizard_step_order_and_skip_filled()
    test_admin_ask_templates_format_all_langs()
    test_format_admin_text_field_prompt_route()
    test_wizard_steps_tuple_matches_handlers()
    asyncio.run(_test_advance_after_title_skips_to_route_when_description_in_buffer())
    asyncio.run(_test_summary_after_source_offers_translate_button())
    print("OK admin wizard flow")
