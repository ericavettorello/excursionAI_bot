from __future__ import annotations

from excursion_ai.catalog import load_catalog
from excursion_ai.console import configure_console
from excursion_ai.i18n import load_i18n
from excursion_ai.texts import format_excursion_card


def validate_project_data() -> None:
    i18n = load_i18n()
    catalog = load_catalog()
    if not catalog.all():
        raise RuntimeError("Каталог экскурсий пуст.")
    # Проверяем, что карточки можно собрать для всех языков.
    for excursion in catalog.all():
        for lang in ("ru", "en", "it"):
            excursion.title(lang)
            excursion.description(lang)
            excursion.city(lang)
            excursion.meeting_point(lang)
            excursion.route(lang)
            i18n.t(lang, f"tour_language.{excursion.conducted_language}")
            format_excursion_card(excursion, lang, i18n)
    print("Проверка данных пройдена:")
    print(f"- переводы: ru, en, it, ключей {i18n.key_count()}")
    print(f"- экскурсий в каталоге: {len(catalog.all())}")
    print(f"- идентификаторы: {', '.join(item.id for item in catalog.all())}")


if __name__ == "__main__":
    configure_console()
    validate_project_data()
