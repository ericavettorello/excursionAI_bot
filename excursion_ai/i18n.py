from __future__ import annotations

import json
from pathlib import Path
from string import Formatter

from excursion_ai.config import LOCALES_DIR, SUPPORTED_LANGS


class I18nError(Exception):
    """Ошибка загрузки или проверки переводов."""


def template_field_names(template: str) -> frozenset[str]:
    """Имена полей в шаблоне str.format (без подстановки значений)."""
    names: set[str] = set()
    for _, field_name, _, _ in Formatter().parse(template):
        if field_name:
            names.add(field_name)
    return frozenset(names)


class I18n:
    def __init__(self, locales: dict[str, dict[str, str]]) -> None:
        self._locales = locales

    def t(self, lang: str, key: str, **kwargs: object) -> str:
        bundle = self._locales.get(lang) or self._locales["ru"]
        template = bundle.get(key)
        if template is None:
            template = self._locales["ru"].get(key, key)
        if not kwargs:
            return template
        return template.format(**kwargs)

    def key_count(self) -> int:
        return len(self._locales["ru"])


def _load_locale(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise I18nError(f"Не найден файл перевода: {path.name}")
    with path.open(encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise I18nError(f"Файл {path.name} должен содержать объект JSON.")
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise I18nError(f"В {path.name} все ключи и значения должны быть строками.")
    return data


def validate_locale_keys(locales: dict[str, dict[str, str]]) -> None:
    keys_by_lang = {lang: set(bundle.keys()) for lang, bundle in locales.items()}
    missing_langs = [lang for lang in SUPPORTED_LANGS if lang not in keys_by_lang]
    if missing_langs:
        raise I18nError(f"Отсутствуют переводы: {', '.join(missing_langs)}")

    reference = keys_by_lang[SUPPORTED_LANGS[0]]
    if not reference:
        raise I18nError("Набор ключей переводов пуст.")

    problems: list[str] = []
    for lang in SUPPORTED_LANGS[1:]:
        current = keys_by_lang[lang]
        missing = sorted(reference - current)
        extra = sorted(current - reference)
        if missing:
            problems.append(f"{lang}: нет ключей {', '.join(missing)}")
        if extra:
            problems.append(f"{lang}: лишние ключи {', '.join(extra)}")
    if problems:
        raise I18nError("Наборы ключей переводов не совпадают. " + "; ".join(problems))


def load_i18n(locales_dir: Path = LOCALES_DIR) -> I18n:
    locales = {
        lang: _load_locale(locales_dir / f"{lang}.json")
        for lang in SUPPORTED_LANGS
    }
    validate_locale_keys(locales)
    return I18n(locales)
