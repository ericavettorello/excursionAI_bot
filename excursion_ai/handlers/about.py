from __future__ import annotations

import logging

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from excursion_ai.ai.service import AIConsultantService
from excursion_ai.config import Settings
from excursion_ai.handlers.apply_flow import ask_conflict, is_in_form
from excursion_ai.handlers.common import MenuText
from excursion_ai.handlers.consult_state import leave_consult_mode
from excursion_ai.handlers.start import require_language
from excursion_ai.i18n import I18n
from excursion_ai.keyboards import main_menu
from excursion_ai.languages import LanguageStore
from excursion_ai.log_context import log_event

router = Router(name="about")
logger = logging.getLogger(__name__)


@router.message(MenuText("menu.about"))
async def show_about(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    ai: AIConsultantService,
    settings: Settings | None = None,
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
            pending_action="menu_about",
        )
        return
    await leave_consult_mode(state)
    log_event(
        logger,
        action="menu.about",
        stage="open",
        result="ok",
        lang=lang,
        ai_ready=ai.is_available(),
    )
    about_key = "about.text_ai_ready" if ai.is_available() else "about.text"
    is_admin = bool(
        message.from_user and settings is not None and settings.is_admin(message.from_user.id)
    )
    await message.answer(
        i18n.t(lang, about_key),
        reply_markup=main_menu(lang, i18n, is_admin=is_admin),
    )
