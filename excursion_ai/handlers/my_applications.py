from __future__ import annotations

import logging

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from excursion_ai.db.applications import ApplicationRepository
from excursion_ai.forms.texts import format_application_list_item
from excursion_ai.handlers.apply_flow import ask_conflict, is_in_form
from excursion_ai.handlers.common import MenuText
from excursion_ai.handlers.consult_state import leave_consult_mode
from excursion_ai.handlers.start import require_language, send_welcome
from excursion_ai.i18n import I18n
from excursion_ai.keyboards import MyAppsCallback, main_menu, my_apps_keyboard
from excursion_ai.languages import LanguageStore
from excursion_ai.log_context import log_event

router = Router(name="my_applications")
logger = logging.getLogger(__name__)

PAGE_SIZE = 5


def _render_page(
    *,
    lang: str,
    i18n: I18n,
    items: list,
    page: int,
) -> tuple[str, int]:
    pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE) if items else 1
    page = max(0, min(page, pages - 1))
    if not items:
        return i18n.t(lang, "my.empty"), pages

    chunk = items[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    blocks = [
        format_application_list_item(
            lang=lang,
            i18n=i18n,
            public_number=app.public_number,
            excursion_title=app.excursion_title,
            requested_date=app.requested_date,
            participants_count=app.participants_count,
            unit_price=app.unit_price,
            currency=app.currency,
            status=app.status,
        )
        for app in chunk
    ]
    header = f"{i18n.t(lang, 'my.title')}\n{i18n.t(lang, 'my.page', page=page + 1, pages=pages)}"
    return header + "\n\n" + "\n\n".join(blocks), pages


@router.message(MenuText("menu.my_applications"))
async def show_my_applications(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    applications: ApplicationRepository,
) -> None:
    lang = await require_language(message, langs, i18n)
    if not lang or not message.from_user:
        return
    if await is_in_form(state):
        await ask_conflict(
            message,
            lang=lang,
            i18n=i18n,
            state=state,
            pending_action="menu_my_applications",
        )
        return
    await leave_consult_mode(state)
    await send_my_applications_page(
        message,
        lang=lang,
        i18n=i18n,
        applications=applications,
        page=0,
        telegram_user_id=message.from_user.id,
    )


async def send_my_applications_page(
    target: Message,
    *,
    lang: str,
    i18n: I18n,
    applications: ApplicationRepository,
    page: int,
    telegram_user_id: int,
) -> None:
    items = await applications.list_for_user(telegram_user_id)
    text, pages = _render_page(lang=lang, i18n=i18n, items=items, page=page)
    empty = len(items) == 0
    log_event(
        logger,
        action="application.list_ui",
        stage="page",
        result="ok",
        page=page,
        count=len(items),
    )
    await target.answer(
        text,
        reply_markup=my_apps_keyboard(
            lang,
            i18n,
            page=page,
            pages=pages,
            empty=empty,
        ),
        parse_mode="HTML",
    )


@router.callback_query(MyAppsCallback.filter())
async def my_apps_callbacks(
    callback: CallbackQuery,
    callback_data: MyAppsCallback,
    langs: LanguageStore,
    i18n: I18n,
    applications: ApplicationRepository,
) -> None:
    if not callback.from_user or not isinstance(callback.message, Message):
        await callback.answer()
        return
    lang = await langs.get(callback.from_user.id)
    if not lang:
        await callback.answer(i18n.t("ru", "error.choose_language"), show_alert=True)
        return

    if callback_data.action == "menu":
        await callback.answer()
        await send_welcome(callback.message, lang, i18n)
        return

    if callback_data.action != "page":
        await callback.answer()
        return

    items = await applications.list_for_user(callback.from_user.id)
    text, pages = _render_page(
        lang=lang,
        i18n=i18n,
        items=items,
        page=callback_data.page,
    )
    empty = len(items) == 0
    page = max(0, min(callback_data.page, pages - 1))
    await callback.message.edit_text(
        text,
        reply_markup=my_apps_keyboard(
            lang,
            i18n,
            page=page,
            pages=pages,
            empty=empty,
        ),
        parse_mode="HTML",
    )
    await callback.answer()
