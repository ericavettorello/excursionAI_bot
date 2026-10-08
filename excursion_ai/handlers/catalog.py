from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InputMediaPhoto, Message

from excursion_ai.catalog import Catalog, CatalogError, Excursion
from excursion_ai.db.sessions import SessionRepository, format_rome_datetime
from excursion_ai.handlers.apply_flow import ask_conflict, is_in_form
from excursion_ai.handlers.common import MenuText
from excursion_ai.handlers.consult_state import leave_consult_mode
from excursion_ai.handlers.start import require_language, send_welcome
from excursion_ai.i18n import I18n
from excursion_ai.keyboards import (
    ExcursionCallback,
    excursion_card_keyboard,
    excursions_keyboard,
)
from excursion_ai.languages import LanguageStore
from excursion_ai.log_context import get_event_id, log_event, log_exception
from excursion_ai.texts import format_excursion_card

router = Router(name="catalog")
logger = logging.getLogger(__name__)


def catalog_list_text(lang: str, i18n: I18n) -> str:
    return f"{i18n.t(lang, 'catalog.title')}\n\n{i18n.t(lang, 'catalog.choose')}"


def _excursion_photo_ids(excursion: Excursion) -> list[str]:
    if excursion.photos:
        return [photo.file_id for photo in excursion.photos[:3]]
    if excursion.photo_file_id:
        return [excursion.photo_file_id]
    return []


async def send_excursion_card(
    message: Message,
    *,
    excursion: Excursion,
    lang: str,
    i18n: I18n,
    sessions: SessionRepository | None = None,
) -> None:
    """Отправляет фото (1 или альбом 2–3) и текстовую карточку отдельным сообщением."""
    schedule_labels: list[str] = []
    if sessions is not None:
        rows = await sessions.list_upcoming_active(excursion.id)
        schedule_labels = [format_rome_datetime(row.starts_at) for row in rows]
    text = format_excursion_card(
        excursion, lang, i18n, schedule_labels=schedule_labels
    )
    markup = excursion_card_keyboard(lang, i18n, excursion.id)
    photo_ids = _excursion_photo_ids(excursion)
    if len(photo_ids) == 1:
        await message.answer_photo(photo_ids[0])
    elif len(photo_ids) > 1:
        media = [InputMediaPhoto(media=file_id) for file_id in photo_ids]
        await message.answer_media_group(media)
    await message.answer(text, reply_markup=markup, parse_mode="HTML")


async def send_catalog_list(
    message: Message,
    lang: str,
    i18n: I18n,
    catalog: Catalog,
) -> None:
    try:
        items = catalog.all()
        if not items:
            raise CatalogError("Каталог экскурсий пуст.")
        await message.answer(
            catalog_list_text(lang, i18n),
            reply_markup=excursions_keyboard(catalog, lang, i18n),
        )
        log_event(
            logger,
            action="catalog.open",
            stage="list",
            result="ok",
            count=len(items),
            lang=lang,
        )
    except CatalogError as error:
        error_id = get_event_id()
        log_exception(
            logger,
            action="catalog.open",
            stage="list",
            exc=error,
            result="error",
            error_id=error_id,
        )
        await message.answer(i18n.t(lang, "error.with_ref", error_id=error_id))


@router.message(MenuText("menu.excursions"))
async def show_catalog(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    catalog: Catalog,
) -> None:
    lang = await require_language(message, langs, i18n)
    if not lang:
        return
    if await is_in_form(state):
        await ask_conflict(
            message,
            lang=lang,
            i18n=i18n,
            state=state,
            pending_action="menu_excursions",
        )
        return
    await leave_consult_mode(state)
    await send_catalog_list(message, lang, i18n, catalog)


@router.callback_query(ExcursionCallback.filter(F.action.in_({"view", "list", "menu"})))
async def handle_excursion_callback(
    callback: CallbackQuery,
    callback_data: ExcursionCallback,
    langs: LanguageStore,
    i18n: I18n,
    catalog: Catalog,
    sessions: SessionRepository,
) -> None:
    if not callback.from_user or not isinstance(callback.message, Message):
        await callback.answer()
        return

    lang = await langs.get(callback.from_user.id)
    if not lang:
        log_event(
            logger,
            action="catalog.callback",
            stage="language_missing",
            result="prompt",
            callback_action=callback_data.action,
        )
        await callback.answer(i18n.t("ru", "error.choose_language"), show_alert=True)
        await callback.message.answer(i18n.t("ru", "language.choose"))
        return

    try:
        if callback_data.action == "menu":
            log_event(
                logger,
                action="menu.open",
                stage="from_catalog",
                result="ok",
                lang=lang,
            )
            await callback.answer()
            await send_welcome(callback.message, lang, i18n)
            return

        if callback_data.action == "list":
            await callback.message.edit_text(
                catalog_list_text(lang, i18n),
                reply_markup=excursions_keyboard(catalog, lang, i18n),
            )
            await callback.answer()
            log_event(
                logger,
                action="catalog.open",
                stage="list_back",
                result="ok",
                count=len(catalog.all()),
                lang=lang,
            )
            return

        excursion = catalog.get(callback_data.excursion_id)
        if excursion is None:
            log_event(
                logger,
                action="catalog.view",
                stage="missing",
                result="not_found",
                level=logging.WARNING,
                excursion_id=callback_data.excursion_id,
            )
            await callback.answer(i18n.t(lang, "catalog.not_found"), show_alert=True)
            return

        await callback.answer()
        await send_excursion_card(
            callback.message,
            excursion=excursion,
            lang=lang,
            i18n=i18n,
            sessions=sessions,
        )
        log_event(
            logger,
            action="catalog.view",
            stage="card",
            result="ok",
            excursion_id=excursion.id,
            lang=lang,
            photos=len(_excursion_photo_ids(excursion)),
        )
    except CatalogError as error:
        error_id = get_event_id()
        log_exception(
            logger,
            action="catalog.open",
            stage="callback",
            exc=error,
            result="error",
            error_id=error_id,
        )
        await callback.answer(i18n.t(lang, "error.catalog"), show_alert=True)
        await callback.message.answer(i18n.t(lang, "error.with_ref", error_id=error_id))
