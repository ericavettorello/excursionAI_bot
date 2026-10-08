from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import Message

from excursion_ai.handlers.common import current_lang
from excursion_ai.handlers.start import send_language_prompt
from excursion_ai.i18n import I18n
from excursion_ai.keyboards import main_menu
from excursion_ai.languages import LanguageStore
from excursion_ai.log_context import log_event

router = Router(name="fallback")
logger = logging.getLogger(__name__)


@router.message()
async def unknown_message(message: Message, langs: LanguageStore, i18n: I18n) -> None:
    lang = await current_lang(message, langs)
    if not lang:
        log_event(
            logger,
            action="message.unknown",
            stage="language_missing",
            result="prompt",
        )
        await send_language_prompt(message, i18n)
        return
    log_event(
        logger,
        action="message.unknown",
        stage="hint",
        result="ok",
        lang=lang,
    )
    await message.answer(
        i18n.t(lang, "error.unknown"),
        reply_markup=main_menu(lang, i18n),
    )
