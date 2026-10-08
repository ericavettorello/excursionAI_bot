"""Проверки готовности переводов и итоговой карточки админ-визарда."""

from __future__ import annotations

from decimal import Decimal

from excursion_ai.catalog import Excursion
from excursion_ai.handlers.admin import (
    _build_summary_text,
    _missing_required,
    _ready_map,
    _translation_ready,
)
from excursion_ai.i18n import load_i18n


def _draft(**kwargs: object) -> Excursion:
    base = {
        "id": "demo-draft",
        "city_id": "rome",
        "duration_minutes": 60,
        "price_per_person": Decimal("70"),
        "currency": "EUR",
        "conducted_languages": ("en", "it"),
        "translations": {},
        "status": "draft",
        "version": 1,
        "source_language": "ru",
    }
    base.update(kwargs)
    return Excursion(**base)  # type: ignore[arg-type]


def test_translation_ready_and_missing() -> None:
    i18n = load_i18n()
    excursion = _draft(
        translations={
            "ru": {
                "title": "Знакомство с Римом",
                "city": "rome",
                "description": "Описание достаточно длинное",
                "route": ["a", "b", "c"],
                "meeting_point": "Колизей",
            }
        }
    )
    assert _translation_ready(excursion, "ru") is True
    assert _translation_ready(excursion, "en") is False
    assert _ready_map(excursion) == {"ru": True, "en": False, "it": False}
    missing = _missing_required(excursion, lang="ru", i18n=i18n)
    assert len(missing) == 8
    text = _build_summary_text(excursion, lang="ru", i18n=i18n)
    assert "Знакомство с Римом" in text
    assert "RU: готово" in text
    assert "EN: не заполнено" in text
    assert "Фото: 0/3" in text
    assert "Исходный язык: RU" in text


def test_complete_draft_has_no_missing() -> None:
    i18n = load_i18n()
    block = {
        "title": "Title",
        "city": "rome",
        "description": "Description long enough",
        "route": ["a", "b", "c"],
        "meeting_point": "Meet here",
    }
    excursion = _draft(
        translations={"ru": block, "en": block, "it": block},
        conducted_languages=("ru",),
    )
    assert _missing_required(excursion, lang="en", i18n=i18n) == []
    assert all(_ready_map(excursion).values())


if __name__ == "__main__":
    test_translation_ready_and_missing()
    test_complete_draft_has_no_missing()
    print("OK admin wizard helpers")
