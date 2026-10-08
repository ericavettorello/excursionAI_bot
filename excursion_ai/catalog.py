from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from excursion_ai.config import CATALOG_PATH, SUPPORTED_LANGS

REQUIRED_TRANSLATION_FIELDS = ("title", "city", "description", "meeting_point")


class CatalogError(Exception):
    """Ошибка загрузки или проверки каталога."""


@dataclass(frozen=True)
class ExcursionPhoto:
    file_id: str
    file_unique_id: str
    sort_order: int = 0


@dataclass(frozen=True)
class TranslationMeta:
    review_status: str = "confirmed"
    origin: str = "manual"
    source_fingerprint: str | None = None


@dataclass(frozen=True)
class Excursion:
    id: str
    city_id: str
    duration_minutes: int
    price_per_person: Decimal
    currency: str
    conducted_languages: tuple[str, ...]
    translations: dict[str, dict[str, object]]
    photo_file_id: str | None = None
    photos: tuple[ExcursionPhoto, ...] = ()
    source_language: str | None = None
    translation_meta: dict[str, TranslationMeta] = field(default_factory=dict)
    version: int = 1
    status: str = "published"

    @property
    def conducted_language(self) -> str:
        return self.conducted_languages[0] if self.conducted_languages else "ru"

    @property
    def photo_count(self) -> int:
        if self.photos:
            return len(self.photos)
        return 1 if self.photo_file_id else 0

    def localized(self, lang: str) -> dict[str, object]:
        return self.translations.get(lang) or self.translations["ru"]

    def title(self, lang: str) -> str:
        return str(self.localized(lang)["title"])

    def city(self, lang: str) -> str:
        return str(self.localized(lang)["city"])

    def description(self, lang: str) -> str:
        return str(self.localized(lang)["description"])

    def meeting_point(self, lang: str) -> str:
        return str(self.localized(lang)["meeting_point"])

    def route(self, lang: str) -> list[str]:
        items = self.localized(lang)["route"]
        return [str(item) for item in items]  # type: ignore[union-attr]

    def duration_hours(self) -> int:
        return max(1, self.duration_minutes // 60) if self.duration_minutes >= 60 else 1

    def meta_for(self, lang: str) -> TranslationMeta:
        return self.translation_meta.get(lang) or TranslationMeta()


class Catalog:
    def __init__(self, excursions: list[Excursion]) -> None:
        self._excursions = {item.id: item for item in excursions}

    def all(self) -> list[Excursion]:
        return list(self._excursions.values())

    def get(self, excursion_id: str) -> Excursion | None:
        return self._excursions.get(excursion_id)

    def ids(self) -> set[str]:
        return set(self._excursions)


def _require(raw: dict, field: str, excursion_id: str) -> object:
    if field not in raw:
        raise CatalogError(f"У экскурсии {excursion_id} нет поля {field}.")
    return raw[field]


def _parse_excursion(raw: dict) -> Excursion:
    if not isinstance(raw, dict):
        raise CatalogError("Каждая экскурсия должна быть объектом JSON.")

    excursion_id = str(_require(raw, "id", "без id")).strip()
    if not excursion_id:
        raise CatalogError("Идентификатор экскурсии не может быть пустым.")

    duration = _require(raw, "duration_minutes", excursion_id)
    price = _require(raw, "price_per_person", excursion_id)
    if not isinstance(duration, int) or duration <= 0:
        raise CatalogError(f"У {excursion_id} некорректная продолжительность.")
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        raise CatalogError(f"У {excursion_id} некорректная цена.")
    if price < 0:
        raise CatalogError(f"У {excursion_id} цена не может быть отрицательной.")

    currency = str(_require(raw, "currency", excursion_id))
    if currency != "EUR":
        raise CatalogError(f"У {excursion_id} валюта должна быть EUR.")

    languages_raw = raw.get("conducted_languages")
    if languages_raw is None:
        conducted = str(_require(raw, "conducted_language", excursion_id))
        languages = (conducted,)
    else:
        if not isinstance(languages_raw, list) or not languages_raw:
            raise CatalogError(f"У {excursion_id} некорректный список языков проведения.")
        languages = tuple(str(item) for item in languages_raw)
    for lang in languages:
        if lang not in SUPPORTED_LANGS:
            raise CatalogError(f"У {excursion_id} неизвестный язык проведения: {lang}.")

    translations: dict[str, dict[str, object]] = {}
    for lang in SUPPORTED_LANGS:
        block = raw.get(lang)
        if not isinstance(block, dict):
            raise CatalogError(f"У {excursion_id} нет блока перевода {lang}.")
        for field in REQUIRED_TRANSLATION_FIELDS:
            if field not in block or not str(block[field]).strip():
                raise CatalogError(f"У {excursion_id}/{lang} нет поля {field}.")
        route = block.get("route")
        if not isinstance(route, list) or not 3 <= len(route) <= 4:
            raise CatalogError(f"У {excursion_id}/{lang} маршрут должен содержать 3–4 пункта.")
        if any(not isinstance(item, str) or not item.strip() for item in route):
            raise CatalogError(f"У {excursion_id}/{lang} пункты маршрута должны быть строками.")
        translations[lang] = block

    return Excursion(
        id=excursion_id,
        city_id=str(_require(raw, "city", excursion_id)),
        duration_minutes=duration,
        price_per_person=Decimal(str(price)),
        currency=currency,
        conducted_languages=languages,
        translations=translations,
        status="published",
    )


def load_catalog_from_json(path: Path = CATALOG_PATH) -> Catalog:
    """Валидация и чтение seed-файла. Не используется как runtime-источник."""
    if not path.is_file():
        raise CatalogError(f"Не найден файл каталога: {path}")
    with path.open(encoding="utf-8") as file:
        raw = json.load(file)
    if not isinstance(raw, list) or not raw:
        raise CatalogError("Каталог должен быть непустым списком экскурсий.")

    excursions = [_parse_excursion(item) for item in raw]
    ids = [item.id for item in excursions]
    if len(ids) != len(set(ids)):
        raise CatalogError("В каталоге повторяются идентификаторы экскурсий.")
    return Catalog(excursions)


# Совместимость со старыми импортами в тестах seed/JSON.
load_catalog = load_catalog_from_json
