from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from excursion_ai.catalog import Catalog
from excursion_ai.config import SUPPORTED_LANGS
from excursion_ai.i18n import I18n


class ExcursionCallback(CallbackData, prefix="exc"):
    action: str
    excursion_id: str = ""


class ApplyCallback(CallbackData, prefix="apl"):
    action: str
    field: str = ""


class SchedulePickCallback(CallbackData, prefix="sch"):
    action: str
    day: str = ""
    session_id: str = ""


class AdminScheduleCallback(CallbackData, prefix="asch"):
    """Telegram limit 64 bytes: for session actions pack session_id only."""

    action: str
    excursion_id: str = ""
    session_id: str = ""
    ret: str = ""


class MyAppsCallback(CallbackData, prefix="mya"):
    action: str
    page: int = 0


class AdminCallback(CallbackData, prefix="adm"):
    action: str
    excursion_id: str = ""
    page: int = 0
    field: str = ""
    lang: str = ""


def language_keyboard(i18n: I18n) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text=i18n.t("ru", "language.button.ru")),
        KeyboardButton(text=i18n.t("ru", "language.button.en")),
        KeyboardButton(text=i18n.t("ru", "language.button.it")),
    )
    return builder.as_markup(resize_keyboard=True)


def main_menu(lang: str, i18n: I18n, *, is_admin: bool = False) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text=i18n.t(lang, "menu.excursions")))
    builder.row(KeyboardButton(text=i18n.t(lang, "menu.ask_ai")))
    builder.row(KeyboardButton(text=i18n.t(lang, "menu.my_applications")))
    builder.row(KeyboardButton(text=i18n.t(lang, "menu.about")))
    if is_admin:
        builder.row(KeyboardButton(text=i18n.t(lang, "menu.admin")))
    builder.row(KeyboardButton(text=i18n.t(lang, "menu.change_language")))
    return builder.as_markup(resize_keyboard=True)


def consult_keyboard(lang: str, i18n: I18n) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text=i18n.t(lang, "ai.clear")))
    builder.row(KeyboardButton(text=i18n.t(lang, "ai.back_menu")))
    return builder.as_markup(resize_keyboard=True)


def cancel_keyboard(lang: str, i18n: I18n) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text=i18n.t(lang, "apply.cancel")))
    return builder.as_markup(resize_keyboard=True)


def phone_keyboard(lang: str, i18n: I18n) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text=i18n.t(lang, "apply.skip_phone")))
    builder.row(KeyboardButton(text=i18n.t(lang, "apply.cancel")))
    return builder.as_markup(resize_keyboard=True)


def remove_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


def excursions_keyboard(catalog: Catalog, lang: str, i18n: I18n) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for excursion in catalog.all():
        builder.button(
            text=excursion.title(lang),
            callback_data=ExcursionCallback(action="view", excursion_id=excursion.id),
        )
    builder.button(
        text=i18n.t(lang, "catalog.back_to_menu"),
        callback_data=ExcursionCallback(action="menu"),
    )
    builder.adjust(1)
    return builder.as_markup()


def excursion_card_keyboard(
    lang: str,
    i18n: I18n,
    excursion_id: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=i18n.t(lang, "catalog.apply"),
        callback_data=ExcursionCallback(action="apply", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "catalog.back_to_list"),
        callback_data=ExcursionCallback(action="list"),
    )
    builder.button(
        text=i18n.t(lang, "catalog.back_to_menu"),
        callback_data=ExcursionCallback(action="menu"),
    )
    builder.adjust(1)
    return builder.as_markup()


def excursion_recommend_keyboard(
    lang: str,
    i18n: I18n,
    excursion_id: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=i18n.t(lang, "catalog.open"),
        callback_data=ExcursionCallback(action="view", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "catalog.apply"),
        callback_data=ExcursionCallback(action="apply", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "catalog.back_to_menu"),
        callback_data=ExcursionCallback(action="menu"),
    )
    builder.adjust(1)
    return builder.as_markup()


def confirm_keyboard(lang: str, i18n: I18n) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=i18n.t(lang, "apply.confirm"),
        callback_data=ApplyCallback(action="confirm"),
    )
    builder.button(
        text=i18n.t(lang, "apply.edit"),
        callback_data=ApplyCallback(action="edit"),
    )
    builder.button(
        text=i18n.t(lang, "apply.cancel"),
        callback_data=ApplyCallback(action="cancel"),
    )
    builder.adjust(1)
    return builder.as_markup()


def edit_field_keyboard(lang: str, i18n: I18n) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=i18n.t(lang, "apply.edit_date"),
        callback_data=ApplyCallback(action="edit_field", field="date"),
    )
    builder.button(
        text=i18n.t(lang, "apply.edit_participants"),
        callback_data=ApplyCallback(action="edit_field", field="participants"),
    )
    builder.button(
        text=i18n.t(lang, "apply.edit_name"),
        callback_data=ApplyCallback(action="edit_field", field="name"),
    )
    builder.button(
        text=i18n.t(lang, "apply.edit_phone"),
        callback_data=ApplyCallback(action="edit_field", field="phone"),
    )
    builder.button(
        text=i18n.t(lang, "apply.cancel"),
        callback_data=ApplyCallback(action="cancel"),
    )
    builder.adjust(1)
    return builder.as_markup()


def conflict_keyboard(lang: str, i18n: I18n) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=i18n.t(lang, "apply.continue"),
        callback_data=ApplyCallback(action="continue"),
    )
    builder.button(
        text=i18n.t(lang, "apply.discard"),
        callback_data=ApplyCallback(action="discard"),
    )
    builder.adjust(1)
    return builder.as_markup()


def my_apps_keyboard(
    lang: str,
    i18n: I18n,
    *,
    page: int,
    pages: int,
    empty: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if empty:
        builder.button(
            text=i18n.t(lang, "my.open_catalog"),
            callback_data=ExcursionCallback(action="list"),
        )
    else:
        if page > 0:
            builder.button(
                text=i18n.t(lang, "my.prev"),
                callback_data=MyAppsCallback(action="page", page=page - 1),
            )
        if page + 1 < pages:
            builder.button(
                text=i18n.t(lang, "my.next"),
                callback_data=MyAppsCallback(action="page", page=page + 1),
            )
    builder.button(
        text=i18n.t(lang, "catalog.back_to_menu"),
        callback_data=MyAppsCallback(action="menu"),
    )
    builder.adjust(2, 1)
    return builder.as_markup()


def all_cancel_texts(i18n: I18n) -> set[str]:
    from excursion_ai.handlers.common import LEGACY_CANCEL

    texts = {i18n.t(lang, "apply.cancel") for lang in SUPPORTED_LANGS}
    texts |= set(LEGACY_CANCEL)
    return texts


def all_skip_phone_texts(i18n: I18n) -> set[str]:
    from excursion_ai.handlers.common import LEGACY_SKIP_PHONE

    texts = {i18n.t(lang, "apply.skip_phone") for lang in SUPPORTED_LANGS}
    texts |= set(LEGACY_SKIP_PHONE)
    return texts


def all_consult_nav_texts(i18n: I18n) -> set[str]:
    texts: set[str] = set()
    for lang in SUPPORTED_LANGS:
        texts.add(i18n.t(lang, "ai.clear"))
        texts.add(i18n.t(lang, "ai.back_menu"))
    return texts


ADMIN_PAGE_SIZE = 5


def admin_main_keyboard(lang: str, i18n: I18n) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=i18n.t(lang, "admin.btn.create"),
        callback_data=AdminCallback(action="create"),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.schedule"),
        callback_data=AdminScheduleCallback(action="home"),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.list_published"),
        callback_data=AdminCallback(action="list", field="published"),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.list_drafts"),
        callback_data=AdminCallback(action="list", field="draft"),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.list_archived"),
        callback_data=AdminCallback(action="list", field="archived"),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.back_menu"),
        callback_data=AdminCallback(action="menu"),
    )
    builder.adjust(1)
    return builder.as_markup()


def schedule_dates_keyboard(
    lang: str,
    i18n: I18n,
    days: list[str],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for day in days:
        builder.button(
            text=day,
            callback_data=SchedulePickCallback(action="date", day=day),
        )
    builder.button(
        text=i18n.t(lang, "apply.cancel"),
        callback_data=ApplyCallback(action="cancel"),
    )
    builder.adjust(2)
    return builder.as_markup()


def schedule_times_keyboard(
    lang: str,
    i18n: I18n,
    slots: list[tuple[str, str]],
) -> InlineKeyboardMarkup:
    """slots: list of (session_id, label HH:MMfree)."""
    builder = InlineKeyboardBuilder()
    for session_id, label in slots:
        builder.button(
            text=label,
            callback_data=SchedulePickCallback(action="time", session_id=session_id),
        )
    builder.button(
        text=i18n.t(lang, "apply.back_to_dates"),
        callback_data=SchedulePickCallback(action="back_dates"),
    )
    builder.button(
        text=i18n.t(lang, "apply.cancel"),
        callback_data=ApplyCallback(action="cancel"),
    )
    builder.adjust(2)
    return builder.as_markup()


def admin_schedule_excursions_keyboard(
    lang: str,
    i18n: I18n,
    items: list[tuple[str, str]],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for excursion_id, title in items:
        builder.button(
            text=title[:60],
            callback_data=AdminScheduleCallback(
                action="excursion", excursion_id=excursion_id
            ),
        )
    builder.button(
        text=i18n.t(lang, "admin.btn.home"),
        callback_data=AdminCallback(action="home"),
    )
    builder.adjust(1)
    return builder.as_markup()


SCHEDULE_PAGE_SIZE = 5


def admin_schedule_list_keyboard(
    lang: str,
    i18n: I18n,
    *,
    excursion_id: str,
    sessions: list[tuple[str, str, bool]],
    mode: str = "menu",
    page: int = 0,
    pages: int = 1,
) -> InlineKeyboardMarkup:
    """mode: menu | wizard | summary | card. Session buttons pack session_id only."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=i18n.t(lang, "admin.schedule.add"),
        callback_data=AdminScheduleCallback(action="add", excursion_id=excursion_id),
    )
    for session_id, label, active in sessions:
        mark = "✓" if active else "×"
        builder.button(
            text=f"{mark} {label}"[:64],
            callback_data=AdminScheduleCallback(action="view", session_id=session_id),
        )
    nav = 0
    if page > 0:
        builder.button(
            text=i18n.t(lang, "admin.btn.prev"),
            callback_data=AdminScheduleCallback(action="page_prev"),
        )
        nav += 1
    if page + 1 < pages:
        builder.button(
            text=i18n.t(lang, "admin.btn.next"),
            callback_data=AdminScheduleCallback(action="page_next"),
        )
        nav += 1
    if mode == "wizard":
        builder.button(
            text=i18n.t(lang, "admin.schedule.finish"),
            callback_data=AdminScheduleCallback(
                action="finish", excursion_id=excursion_id
            ),
        )
        builder.button(
            text=i18n.t(lang, "admin.btn.save_draft"),
            callback_data=AdminCallback(
                action="save_draft_exit", excursion_id=excursion_id
            ),
        )
    elif mode in {"summary", "card"}:
        builder.button(
            text=i18n.t(lang, "admin.btn.back"),
            callback_data=AdminScheduleCallback(
                action="finish", excursion_id=excursion_id
            ),
        )
    else:
        builder.button(
            text=i18n.t(lang, "admin.btn.back"),
            callback_data=AdminScheduleCallback(action="home"),
        )
    builder.adjust(1)
    return builder.as_markup()


def admin_schedule_nav_keyboard(lang: str, i18n: I18n) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=i18n.t(lang, "admin.btn.back"),
        callback_data=AdminScheduleCallback(action="nav_back"),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.cancel"),
        callback_data=AdminScheduleCallback(action="nav_cancel"),
    )
    builder.adjust(2)
    return builder.as_markup()


def admin_schedule_error_keyboard(lang: str, i18n: I18n) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=i18n.t(lang, "admin.schedule.retry"),
        callback_data=AdminScheduleCallback(action="retry"),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.back"),
        callback_data=AdminScheduleCallback(action="nav_back"),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.cancel"),
        callback_data=AdminScheduleCallback(action="nav_cancel"),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_session_card_keyboard(
    lang: str,
    i18n: I18n,
    *,
    excursion_id: str,
    session_id: str,
    is_active: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=i18n.t(lang, "admin.schedule.edit_date"),
        callback_data=AdminScheduleCallback(action="edit_date", session_id=session_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.schedule.edit_time"),
        callback_data=AdminScheduleCallback(action="edit_time", session_id=session_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.schedule.edit_capacity"),
        callback_data=AdminScheduleCallback(
            action="edit_capacity", session_id=session_id
        ),
    )
    builder.button(
        text=i18n.t(lang, "admin.schedule.remove"),
        callback_data=AdminScheduleCallback(action="delete", session_id=session_id),
    )
    builder.button(
        text=i18n.t(
            lang,
            "admin.schedule.deactivate" if is_active else "admin.schedule.activate",
        ),
        callback_data=AdminScheduleCallback(action="toggle", session_id=session_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.back"),
        callback_data=AdminScheduleCallback(
            action="excursion", excursion_id=excursion_id
        ),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_list_keyboard(
    lang: str,
    i18n: I18n,
    *,
    status: str,
    items: list[tuple[str, str]],
    page: int,
    pages: int,
) -> InlineKeyboardMarkup:
    """items: список (excursion_id, label)."""
    builder = InlineKeyboardBuilder()
    for excursion_id, label in items:
        builder.button(
            text=label[:64],
            callback_data=AdminCallback(
                action="view",
                excursion_id=excursion_id,
                field=status,
                page=page,
            ),
        )
    nav_row = 0
    if page > 0:
        builder.button(
            text=i18n.t(lang, "admin.btn.prev"),
            callback_data=AdminCallback(action="list", field=status, page=page - 1),
        )
        nav_row += 1
    if page + 1 < pages:
        builder.button(
            text=i18n.t(lang, "admin.btn.next"),
            callback_data=AdminCallback(action="list", field=status, page=page + 1),
        )
        nav_row += 1
    builder.button(
        text=i18n.t(lang, "admin.btn.home"),
        callback_data=AdminCallback(action="home"),
    )
    if nav_row:
        builder.adjust(*([1] * len(items)), nav_row, 1)
    else:
        builder.adjust(1)
    return builder.as_markup()


def admin_card_keyboard(
    lang: str,
    i18n: I18n,
    *,
    excursion_id: str,
    status: str,
    has_pending: bool,
    publishable: bool,
    list_status: str = "",
    page: int = 0,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if status == "draft":
        builder.button(
            text=i18n.t(lang, "admin.btn.continue_draft"),
            callback_data=AdminCallback(action="continue_draft", excursion_id=excursion_id),
        )
        builder.button(
            text=i18n.t(lang, "admin.btn.summary"),
            callback_data=AdminCallback(action="summary", excursion_id=excursion_id),
        )
        builder.button(
            text=i18n.t(lang, "admin.btn.preview"),
            callback_data=AdminCallback(action="preview", excursion_id=excursion_id),
        )
        builder.button(
            text=i18n.t(lang, "admin.btn.edit"),
            callback_data=AdminCallback(action="edit_menu", excursion_id=excursion_id),
        )
        builder.button(
            text=i18n.t(lang, "admin.btn.translations"),
            callback_data=AdminCallback(action="translate_manual", excursion_id=excursion_id),
        )
        builder.button(
            text=i18n.t(lang, "admin.btn.photo"),
            callback_data=AdminCallback(
                action="edit_field",
                excursion_id=excursion_id,
                field="photo",
            ),
        )
        builder.button(
            text=i18n.t(lang, "admin.btn.schedule"),
            callback_data=AdminScheduleCallback(
                action="open", excursion_id=excursion_id, ret="card"
            ),
        )
        builder.button(
            text=i18n.t(lang, "admin.btn.save_draft"),
            callback_data=AdminCallback(action="save_draft_exit", excursion_id=excursion_id),
        )
        builder.button(
            text=i18n.t(lang, "admin.btn.publish"),
            callback_data=AdminCallback(action="publish", excursion_id=excursion_id),
        )
        builder.button(
            text=i18n.t(lang, "admin.btn.archive"),
            callback_data=AdminCallback(action="archive", excursion_id=excursion_id),
        )
    elif status == "published":
        builder.button(
            text=i18n.t(lang, "admin.btn.preview"),
            callback_data=AdminCallback(action="preview", excursion_id=excursion_id),
        )
        builder.button(
            text=i18n.t(lang, "admin.btn.edit"),
            callback_data=AdminCallback(action="edit_menu", excursion_id=excursion_id),
        )
        builder.button(
            text=i18n.t(lang, "admin.btn.translations"),
            callback_data=AdminCallback(action="translate_manual", excursion_id=excursion_id),
        )
        builder.button(
            text=i18n.t(lang, "admin.btn.schedule"),
            callback_data=AdminScheduleCallback(
                action="open", excursion_id=excursion_id, ret="card"
            ),
        )
        builder.button(
            text=i18n.t(lang, "admin.btn.photo"),
            callback_data=AdminCallback(
                action="edit_field",
                excursion_id=excursion_id,
                field="photo",
            ),
        )
        if has_pending:
            builder.button(
                text=i18n.t(lang, "admin.btn.apply_pending"),
                callback_data=AdminCallback(action="confirm_pending", excursion_id=excursion_id),
            )
            builder.button(
                text=i18n.t(lang, "admin.btn.discard_pending"),
                callback_data=AdminCallback(action="discard_pending", excursion_id=excursion_id),
            )
        builder.button(
            text=i18n.t(lang, "admin.btn.archive"),
            callback_data=AdminCallback(action="archive", excursion_id=excursion_id),
        )
    elif status == "archived":
        builder.button(
            text=i18n.t(lang, "admin.btn.restore"),
            callback_data=AdminCallback(action="restore", excursion_id=excursion_id),
        )
    else:
        builder.button(
            text=i18n.t(lang, "admin.btn.edit"),
            callback_data=AdminCallback(action="edit_menu", excursion_id=excursion_id),
        )
    back_status = list_status or status
    builder.button(
        text=i18n.t(lang, "admin.btn.back_list"),
        callback_data=AdminCallback(action="list", field=back_status, page=page),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.home"),
        callback_data=AdminCallback(action="home"),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_edit_menu_keyboard(
    lang: str,
    i18n: I18n,
    *,
    excursion_id: str,
) -> InlineKeyboardMarkup:
    """Главное меню редактирования уже сохранённой экскурсии."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=i18n.t(lang, "admin.btn.edit_data"),
        callback_data=AdminCallback(
            action="edit_section", excursion_id=excursion_id, field="data"
        ),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.edit_route"),
        callback_data=AdminCallback(
            action="edit_section", excursion_id=excursion_id, field="route"
        ),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.edit_photos"),
        callback_data=AdminCallback(
            action="edit_section", excursion_id=excursion_id, field="photo"
        ),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.edit_schedule"),
        callback_data=AdminScheduleCallback(
            action="open", excursion_id=excursion_id, ret="card"
        ),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.back"),
        callback_data=AdminCallback(action="view", excursion_id=excursion_id),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_edit_data_keyboard(
    lang: str,
    i18n: I18n,
    *,
    excursion_id: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    fields = (
        ("city_id", "admin.field.city_id"),
        ("duration", "admin.field.duration"),
        ("price", "admin.field.price"),
        ("languages", "admin.field.languages"),
    )
    for field, key in fields:
        builder.button(
            text=i18n.t(lang, key),
            callback_data=AdminCallback(
                action="edit_field",
                excursion_id=excursion_id,
                field=field,
            ),
        )
    for code in SUPPORTED_LANGS:
        builder.button(
            text=i18n.t(lang, "admin.field.translation", code=code.upper()),
            callback_data=AdminCallback(
                action="edit_translation",
                excursion_id=excursion_id,
                lang=code,
            ),
        )
    builder.button(
        text=i18n.t(lang, "admin.btn.back_edit"),
        callback_data=AdminCallback(action="edit_menu", excursion_id=excursion_id),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_edit_route_keyboard(
    lang: str,
    i18n: I18n,
    *,
    excursion_id: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for code in SUPPORTED_LANGS:
        builder.button(
            text=i18n.t(lang, "admin.btn.edit_route_lang", code=code.upper()),
            callback_data=AdminCallback(
                action="edit_field",
                excursion_id=excursion_id,
                field="route",
                lang=code,
            ),
        )
    builder.button(
        text=i18n.t(lang, "admin.btn.back_edit"),
        callback_data=AdminCallback(action="edit_menu", excursion_id=excursion_id),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_edit_confirm_keyboard(
    lang: str,
    i18n: I18n,
    *,
    excursion_id: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=i18n.t(lang, "admin.btn.edit_save"),
        callback_data=AdminCallback(
            action="edit_confirm_save", excursion_id=excursion_id
        ),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.edit_discard"),
        callback_data=AdminCallback(
            action="edit_confirm_cancel", excursion_id=excursion_id
        ),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_schedule_linked_keyboard(
    lang: str,
    i18n: I18n,
    *,
    excursion_id: str,
    session_id: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=i18n.t(lang, "admin.schedule.add"),
        callback_data=AdminScheduleCallback(
            action="add", excursion_id=excursion_id
        ),
    )
    builder.button(
        text=i18n.t(lang, "admin.schedule.deactivate_confirm"),
        callback_data=AdminScheduleCallback(
            action="deactivate_confirm",
            session_id=session_id,
        ),
    )
    builder.button(
        text=i18n.t(lang, "admin.schedule.back_list"),
        callback_data=AdminScheduleCallback(
            action="excursion", excursion_id=excursion_id
        ),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_edit_translation_keyboard(
    lang: str,
    i18n: I18n,
    *,
    excursion_id: str,
    target_lang: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for field, key in (
        ("title", "admin.field.title"),
        ("description", "admin.field.description"),
        ("route", "admin.field.route"),
        ("meeting", "admin.field.meeting"),
        ("city_label", "admin.field.city_label"),
    ):
        builder.button(
            text=i18n.t(lang, key),
            callback_data=AdminCallback(
                action="edit_field",
                excursion_id=excursion_id,
                field=field,
                lang=target_lang,
            ),
        )
    builder.button(
        text=i18n.t(lang, "admin.btn.back_edit"),
        callback_data=AdminCallback(action="edit_menu", excursion_id=excursion_id),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_wizard_nav(
    lang: str,
    i18n: I18n,
    *,
    show_skip: bool = False,
    excursion_id: str = "",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if show_skip:
        builder.button(
            text=i18n.t(lang, "admin.btn.skip_photo"),
            callback_data=AdminCallback(action="photo_skip", excursion_id=excursion_id),
        )
    builder.button(
        text=i18n.t(lang, "admin.btn.cancel"),
        callback_data=AdminCallback(action="cancel"),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_source_lang_keyboard(lang: str, i18n: I18n) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for code in SUPPORTED_LANGS:
        builder.button(
            text=code.upper(),
            callback_data=AdminCallback(action="source_lang", lang=code),
        )
    builder.button(
        text=i18n.t(lang, "admin.btn.cancel"),
        callback_data=AdminCallback(action="cancel"),
    )
    builder.adjust(3, 1)
    return builder.as_markup()


def admin_translate_offer_keyboard(
    lang: str,
    i18n: I18n,
    *,
    excursion_id: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=i18n.t(lang, "admin.btn.translate_auto"),
        callback_data=AdminCallback(action="translate_auto", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.translate_manual"),
        callback_data=AdminCallback(action="translate_manual", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.save_draft"),
        callback_data=AdminCallback(action="save_draft_exit", excursion_id=excursion_id),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_translate_review_keyboard(
    lang: str,
    i18n: I18n,
    *,
    excursion_id: str,
    source_lang: str,
    statuses: dict[str, str],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for code in SUPPORTED_LANGS:
        status = statuses.get(code, "missing")
        key = f"admin.btn.tr_status.{status}"
        label = f"{code.upper()}: {i18n.t(lang, key)}"
        builder.button(
            text=label,
            callback_data=AdminCallback(
                action="translate_view",
                excursion_id=excursion_id,
                lang=code,
            ),
        )
        if code != source_lang and status != "missing":
            builder.button(
                text=i18n.t(lang, "admin.btn.translate_confirm", code=code.upper()),
                callback_data=AdminCallback(
                    action="translate_confirm",
                    excursion_id=excursion_id,
                    lang=code,
                ),
            )
        builder.button(
            text=i18n.t(lang, "admin.btn.translate_edit", code=code.upper()),
            callback_data=AdminCallback(
                action="translate_edit",
                excursion_id=excursion_id,
                lang=code,
            ),
        )
    builder.button(
        text=i18n.t(lang, "admin.btn.translate_redo"),
        callback_data=AdminCallback(action="translate_redo", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.photo_stage"),
        callback_data=AdminCallback(action="photo_stage", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.summary"),
        callback_data=AdminCallback(action="summary", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.save_draft"),
        callback_data=AdminCallback(action="save_draft_exit", excursion_id=excursion_id),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_retranslate_confirm_keyboard(
    lang: str,
    i18n: I18n,
    *,
    excursion_id: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=i18n.t(lang, "admin.btn.translate_redo_confirm"),
        callback_data=AdminCallback(
            action="translate_redo_confirm",
            excursion_id=excursion_id,
        ),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.cancel"),
        callback_data=AdminCallback(
            action="translate_manual",
            excursion_id=excursion_id,
        ),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_photo_stage_keyboard(
    lang: str,
    i18n: I18n,
    *,
    excursion_id: str,
    count: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if count > 0:
        builder.button(
            text=i18n.t(lang, "admin.btn.photo_manage"),
            callback_data=AdminCallback(action="photo_manage", excursion_id=excursion_id),
        )
    builder.button(
        text=i18n.t(lang, "admin.btn.photo_done"),
        callback_data=AdminCallback(action="photo_done", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.photo_skip"),
        callback_data=AdminCallback(action="photo_skip", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.save_draft"),
        callback_data=AdminCallback(action="save_draft_exit", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.cancel"),
        callback_data=AdminCallback(action="cancel"),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_photo_manage_keyboard(
    lang: str,
    i18n: I18n,
    *,
    excursion_id: str,
    photos: list[tuple[int, str]],
) -> InlineKeyboardMarkup:
    """photos: list of (index, file_unique_id) — index used in callbacks."""
    builder = InlineKeyboardBuilder()
    for index, _unique in photos:
        n = index + 1
        builder.button(
            text=i18n.t(lang, "admin.btn.photo_cover", n=n),
            callback_data=AdminCallback(
                action="photo_cover",
                excursion_id=excursion_id,
                page=index,
            ),
        )
        builder.button(
            text=i18n.t(lang, "admin.btn.photo_replace", n=n),
            callback_data=AdminCallback(
                action="photo_replace",
                excursion_id=excursion_id,
                page=index,
            ),
        )
        builder.button(
            text=i18n.t(lang, "admin.btn.photo_delete", n=n),
            callback_data=AdminCallback(
                action="photo_delete",
                excursion_id=excursion_id,
                page=index,
            ),
        )
    builder.button(
        text=i18n.t(lang, "admin.btn.photo_back"),
        callback_data=AdminCallback(action="photo_back", excursion_id=excursion_id),
    )
    builder.adjust(3 if photos else 1)
    return builder.as_markup()


def admin_lang_conducted(
    lang: str,
    i18n: I18n,
    selected: set[str] | list[str],
) -> InlineKeyboardMarkup:
    selected_set = set(selected)
    builder = InlineKeyboardBuilder()
    for code in SUPPORTED_LANGS:
        mark = "✓ " if code in selected_set else ""
        builder.button(
            text=f"{mark}{code.upper()}",
            callback_data=AdminCallback(action="lang_toggle", lang=code),
        )
    builder.button(
        text=i18n.t(lang, "admin.btn.langs_done"),
        callback_data=AdminCallback(action="lang_done"),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.cancel"),
        callback_data=AdminCallback(action="cancel"),
    )
    builder.adjust(3, 1, 1)
    return builder.as_markup()


def admin_preview_keyboard(
    lang: str,
    i18n: I18n,
    *,
    excursion_id: str,
    publishable: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=i18n.t(lang, "admin.btn.summary"),
        callback_data=AdminCallback(action="summary", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.edit"),
        callback_data=AdminCallback(action="edit_menu", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.translations"),
        callback_data=AdminCallback(action="translate_manual", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.photo"),
        callback_data=AdminCallback(
            action="edit_field",
            excursion_id=excursion_id,
            field="photo",
        ),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.save_draft"),
        callback_data=AdminCallback(action="save_draft_exit", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.publish"),
        callback_data=AdminCallback(action="publish", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.home"),
        callback_data=AdminCallback(action="home"),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_lang_hub_keyboard(
    lang: str,
    i18n: I18n,
    *,
    excursion_id: str,
    ready: dict[str, bool],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for code in SUPPORTED_LANGS:
        key = (
            f"admin.btn.edit_lang.{code}"
            if ready.get(code)
            else f"admin.btn.add_lang.{code}"
        )
        builder.button(
            text=i18n.t(lang, key),
            callback_data=AdminCallback(
                action="fill_lang",
                excursion_id=excursion_id,
                lang=code,
            ),
        )
    builder.button(
        text=i18n.t(lang, "admin.btn.preview"),
        callback_data=AdminCallback(action="preview", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.summary"),
        callback_data=AdminCallback(action="summary", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.save_draft"),
        callback_data=AdminCallback(action="save_draft_exit", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.cancel"),
        callback_data=AdminCallback(action="cancel"),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_summary_keyboard(
    lang: str,
    i18n: I18n,
    *,
    excursion_id: str,
    show_translate_en_it: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if show_translate_en_it:
        builder.button(
            text=i18n.t(lang, "admin.btn.translate_en_it"),
            callback_data=AdminCallback(
                action="translate_auto", excursion_id=excursion_id
            ),
        )
    builder.button(
        text=i18n.t(lang, "admin.btn.preview"),
        callback_data=AdminCallback(action="preview", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.edit"),
        callback_data=AdminCallback(action="edit_menu", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.translations"),
        callback_data=AdminCallback(action="translate_manual", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.photo"),
        callback_data=AdminCallback(
            action="edit_field",
            excursion_id=excursion_id,
            field="photo",
        ),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.schedule"),
        callback_data=AdminScheduleCallback(
            action="open", excursion_id=excursion_id, ret="summary"
        ),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.save_draft"),
        callback_data=AdminCallback(action="save_draft_exit", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.publish"),
        callback_data=AdminCallback(action="publish", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.home"),
        callback_data=AdminCallback(action="home"),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_publish_confirm_keyboard(
    lang: str,
    i18n: I18n,
    *,
    excursion_id: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=i18n.t(lang, "admin.btn.publish_confirm"),
        callback_data=AdminCallback(action="publish_confirm", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.cancel"),
        callback_data=AdminCallback(action="summary", excursion_id=excursion_id),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_archive_confirm_keyboard(
    lang: str,
    i18n: I18n,
    *,
    excursion_id: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=i18n.t(lang, "admin.btn.archive_confirm"),
        callback_data=AdminCallback(action="archive_confirm", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.cancel"),
        callback_data=AdminCallback(action="view", excursion_id=excursion_id),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_publish_missing_keyboard(
    lang: str,
    i18n: I18n,
    *,
    excursion_id: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=i18n.t(lang, "admin.btn.translations"),
        callback_data=AdminCallback(action="translate_manual", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.edit"),
        callback_data=AdminCallback(action="edit_menu", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.summary"),
        callback_data=AdminCallback(action="summary", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.home"),
        callback_data=AdminCallback(action="home"),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_published_done_keyboard(
    lang: str,
    i18n: I18n,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=i18n.t(lang, "admin.btn.view_catalog"),
        callback_data=AdminCallback(action="view_catalog"),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.home"),
        callback_data=AdminCallback(action="home"),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_confirm_pending_keyboard(
    lang: str,
    i18n: I18n,
    *,
    excursion_id: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=i18n.t(lang, "admin.btn.confirm_apply"),
        callback_data=AdminCallback(action="apply_pending", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.cancel"),
        callback_data=AdminCallback(action="view", excursion_id=excursion_id),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_version_conflict_keyboard(
    lang: str,
    i18n: I18n,
    *,
    excursion_id: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text=i18n.t(lang, "admin.btn.refresh"),
        callback_data=AdminCallback(action="view", excursion_id=excursion_id),
    )
    builder.button(
        text=i18n.t(lang, "admin.btn.home"),
        callback_data=AdminCallback(action="home"),
    )
    builder.adjust(1)
    return builder.as_markup()
