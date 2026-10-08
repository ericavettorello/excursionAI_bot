from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import ErrorEvent, Message

from excursion_ai.i18n import I18n
from excursion_ai.keyboards import language_keyboard, main_menu
from excursion_ai.languages import LanguageStore
from excursion_ai.log_context import (
    get_event_id,
    log_event,
    log_exception,
    new_event_id,
    set_event_id,
)

router = Router(name="errors")
logger = logging.getLogger(__name__)


async def _resolve_lang(event: ErrorEvent, langs: LanguageStore) -> tuple[str, bool]:
    user = None
    update = event.update
    if update.message and update.message.from_user:
        user = update.message.from_user
    elif update.callback_query and update.callback_query.from_user:
        user = update.callback_query.from_user
    if user is None:
        return "ru", False
    lang = await langs.get(user.id)
    if lang:
        return lang, True
    return "ru", False


async def _notify_user(
    event: ErrorEvent,
    *,
    i18n: I18n,
    langs: LanguageStore,
    error_id: str,
) -> None:
    lang, has_lang = await _resolve_lang(event, langs)
    text = i18n.t(lang, "error.with_ref", error_id=error_id)
    markup = main_menu(lang, i18n) if has_lang else language_keyboard(i18n)
    update = event.update

    try:
        if update.callback_query:
            callback = update.callback_query
            try:
                await callback.answer()
            except Exception as notify_error:
                log_exception(
                    logger,
                    action="error.notify",
                    stage="callback_answer",
                    exc=notify_error,
                    result="notify_failed",
                )
            if isinstance(callback.message, Message):
                await callback.message.answer(text, reply_markup=markup)
            else:
                log_event(
                    logger,
                    action="error.notify",
                    stage="skip",
                    result="no_message",
                    level=logging.WARNING,
                )
            return

        if update.message:
            await update.message.answer(text, reply_markup=markup)
            return

        log_event(
            logger,
            action="error.notify",
            stage="skip",
            result="no_chat",
            level=logging.WARNING,
        )
    except Exception as notify_error:
        log_exception(
            logger,
            action="error.notify",
            stage="send",
            exc=notify_error,
            result="notify_failed",
        )


@router.errors()
async def on_unhandled_error(
    event: ErrorEvent,
    i18n: I18n,
    langs: LanguageStore,
    event_id: str | None = None,
) -> bool:
    """Единая точка журналирования необработанных ошибок aiogram."""
    resolved_id = event_id if event_id and event_id != "-" else get_event_id()
    if not resolved_id or resolved_id == "-":
        resolved_id = new_event_id()
    set_event_id(resolved_id)

    log_exception(
        logger,
        action="error.unhandled",
        stage="handler",
        exc=event.exception,
        result="error",
        error_id=resolved_id,
    )
    await _notify_user(event, i18n=i18n, langs=langs, error_id=resolved_id)
    return True
