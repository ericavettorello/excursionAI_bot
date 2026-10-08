from __future__ import annotations

from excursion_ai.catalog import Excursion
from excursion_ai.i18n import I18n


def format_excursion_card(
    excursion: Excursion,
    lang: str,
    i18n: I18n,
    *,
    schedule_labels: list[str] | None = None,
) -> str:
    tour_langs = ", ".join(
        i18n.t(lang, f"tour_language.{code}") for code in excursion.conducted_languages
    )
    route_lines = "\n".join(f"{index}. {item}" for index, item in enumerate(excursion.route(lang), start=1))
    lines = [
        f"<b>{excursion.title(lang)}</b>",
        "",
        excursion.description(lang),
        "",
        i18n.t(lang, "catalog.city", city=excursion.city(lang)),
        i18n.t(lang, "catalog.duration_hours", hours=excursion.duration_hours()),
        i18n.t(
            lang,
            "catalog.price",
            price=str(excursion.price_per_person),
            currency=excursion.currency,
        ),
        i18n.t(lang, "catalog.conducted_language", language=tour_langs),
        i18n.t(lang, "catalog.meeting_point", meeting_point=excursion.meeting_point(lang)),
        "",
        f"<b>{i18n.t(lang, 'catalog.route')}</b>",
        route_lines,
        "",
        f"<b>{i18n.t(lang, 'catalog.schedule')}</b>",
    ]
    if schedule_labels:
        lines.extend(
            i18n.t(lang, "catalog.schedule_item", when=label) for label in schedule_labels
        )
    else:
        lines.append(i18n.t(lang, "catalog.schedule_empty"))
    note = i18n.t(lang, "catalog.demo_note").strip()
    if note:
        lines.extend(["", note])
    return "\n".join(lines)


def format_recommend_card(excursion: Excursion, lang: str, i18n: I18n) -> str:
    """Краткая карточка рекомендации: данные только из каталога."""
    lines = [
        f"<b>{excursion.title(lang)}</b>",
        i18n.t(lang, "catalog.city", city=excursion.city(lang)),
        i18n.t(lang, "catalog.duration_hours", hours=excursion.duration_hours()),
        i18n.t(
            lang,
            "catalog.price",
            price=str(excursion.price_per_person),
            currency=excursion.currency,
        ),
    ]
    note = i18n.t(lang, "catalog.demo_note").strip()
    if note:
        lines.append(note)
    return "\n".join(lines)
