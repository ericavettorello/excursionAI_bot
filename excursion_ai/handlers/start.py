from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from excursion_ai.catalog import Catalog
from excursion_ai.config import Settings
from excursion_ai.forms.states import ConsultForm
from excursion_ai.handlers.apply_flow import is_in_form, resume_current_step
from excursion_ai.handlers.common import LANGUAGE_CODES, MenuText, current_lang
from excursion_ai.i18n import I18n
from excursion_ai.keyboards import consult_keyboard, language_keyboard, main_menu
from excursion_ai.languages import LanguageStore
from excursion_ai.log_context import log_event

router = Router(name="start")
logger = logging.getLogger(__name__)


def _is_admin(message: Message, settings: Settings | None) -> bool:
    return bool(
        message.from_user
        and settings is not None
        and settings.is_admin(message.from_user.id)
    )


async def send_language_prompt(message: Message, i18n: I18n) -> None:
    await message.answer(
        i18n.t("ru", "language.choose"),
        reply_markup=language_keyboard(i18n),
    )


async def send_welcome(
    message: Message,
    lang: str,
    i18n: I18n,
    *,
    is_admin: bool = False,
) -> None:
    await message.answer(
        i18n.t(lang, "start.welcome"),
        reply_markup=main_menu(lang, i18n, is_admin=is_admin),
    )


async def require_language(
    message: Message,
    langs: LanguageStore,
    i18n: I18n,
) -> str | None:
    lang = await current_lang(message, langs)
    if lang:
        return lang
    log_event(
        logger,
        action="language.require",
        stage="missing",
        result="prompt",
    )
    await send_language_prompt(message, i18n)
    return None


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    catalog: Catalog,
    settings: Settings | None = None,
) -> None:
    lang = await current_lang(message, langs)
    if not lang:
        log_event(
            logger,
            action="start.command",
            stage="language_prompt",
            result="ok",
        )
        await send_language_prompt(message, i18n)
        return
    if await is_in_form(state):
        from excursion_ai.handlers.apply_flow import ask_conflict

        await ask_conflict(
            message,
            lang=lang,
            i18n=i18n,
            state=state,
            pending_action="menu_start",
        )
        return
    if await state.get_state() == ConsultForm.active.state:
        await state.set_state(None)
    log_event(
        logger,
        action="start.command",
        stage="welcome",
        result="ok",
        lang=lang,
    )
    await send_welcome(message, lang, i18n, is_admin=_is_admin(message, settings))


@router.message(F.text.in_(set(LANGUAGE_CODES)))
async def choose_language(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    catalog: Catalog,
    settings: Settings | None = None,
) -> None:
    if not message.from_user or not message.text:
        return
    lang = LANGUAGE_CODES[message.text]
    previous = await langs.get(message.from_user.id)
    await langs.set(message.from_user.id, lang)
    action = "language.change" if previous else "language.select"
    log_event(
        logger,
        action=action,
        stage="apply",
        result="ok",
        lang=lang,
        previous=previous or "-",
    )
    await message.answer(i18n.t(lang, "language.changed"))
    if await is_in_form(state):
        await resume_current_step(
            message,
            state=state,
            lang=lang,
            i18n=i18n,
            catalog=catalog,
        )
        return
    if await state.get_state() == ConsultForm.active.state:
        await message.answer(
            i18n.t(lang, "ai.intro"),
            reply_markup=consult_keyboard(lang, i18n),
        )
        return
    await send_welcome(message, lang, i18n, is_admin=_is_admin(message, settings))


@router.message(MenuText("menu.change_language"))
async def change_language(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
) -> None:
    if not await require_language(message, langs, i18n):
        return
    # Смена языка во время формы разрешена: данные сохраняются.
    log_event(
        logger,
        action="language.change",
        stage="prompt",
        result="ok",
        in_form=await is_in_form(state),
    )
    await send_language_prompt(message, i18n)
