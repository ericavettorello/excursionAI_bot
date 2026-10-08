from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from excursion_ai.ai.errors import (
    AIBusyError,
    AIConfigError,
    AIError,
    AILimitError,
    AIUnavailableError,
    AIValidationError,
)
from excursion_ai.ai.service import AIConsultantService
from excursion_ai.catalog import Catalog, Excursion
from excursion_ai.forms.states import ConsultForm
from excursion_ai.handlers.apply_flow import ask_conflict, is_in_form
from excursion_ai.handlers.common import LANGUAGE_CODES, MenuText, all_menu_texts, current_lang
from excursion_ai.handlers.consult_state import leave_consult_mode
from excursion_ai.handlers.start import require_language, send_welcome
from excursion_ai.i18n import I18n
from excursion_ai.keyboards import (
    all_cancel_texts,
    all_consult_nav_texts,
    all_skip_phone_texts,
    consult_keyboard,
    excursion_recommend_keyboard,
)
from excursion_ai.languages import LanguageStore
from excursion_ai.log_context import log_event
from excursion_ai.texts import format_recommend_card

router = Router(name="consult")
logger = logging.getLogger(__name__)


def _is_non_question_text(text: str, i18n: I18n) -> bool:
    reserved = set(LANGUAGE_CODES)
    reserved |= all_menu_texts(i18n)
    reserved |= all_cancel_texts(i18n)
    reserved |= all_skip_phone_texts(i18n)
    reserved |= all_consult_nav_texts(i18n)
    return text in reserved


async def enter_consult_mode(
    message: Message,
    *,
    state: FSMContext,
    lang: str,
    i18n: I18n,
    ai: AIConsultantService,
) -> None:
    await state.set_state(ConsultForm.active)
    log_event(
        logger,
        action="ai.consult",
        stage="enter",
        result="ok" if ai.is_available() else "unavailable",
        lang=lang,
    )
    if not ai.is_available():
        missing = ", ".join(ai.missing_settings) or "AI"
        await message.answer(
            i18n.t(lang, "ai.unavailable", missing=missing),
            reply_markup=consult_keyboard(lang, i18n),
        )
        await message.answer(
            i18n.t(lang, "ai.open_catalog_hint"),
            reply_markup=consult_keyboard(lang, i18n),
        )
        return
    await message.answer(
        i18n.t(lang, "ai.intro"),
        reply_markup=consult_keyboard(lang, i18n),
    )


@router.message(MenuText("menu.ask_ai"))
async def open_consult(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    ai: AIConsultantService,
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
            pending_action="menu_ask_ai",
        )
        return
    await enter_consult_mode(message, state=state, lang=lang, i18n=i18n, ai=ai)


@router.message(StateFilter(ConsultForm.active), MenuText("ai.clear"))
async def clear_dialog(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    ai: AIConsultantService,
) -> None:
    lang = await require_language(message, langs, i18n)
    if not lang or not message.from_user:
        return
    ai.clear_dialog(message.from_user.id)
    log_event(logger, action="ai.consult", stage="clear", result="ok")
    await message.answer(
        i18n.t(lang, "ai.cleared"),
        reply_markup=consult_keyboard(lang, i18n),
    )


@router.message(StateFilter(ConsultForm.active), MenuText("ai.back_menu"))
async def back_to_menu(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
) -> None:
    lang = await require_language(message, langs, i18n)
    if not lang:
        return
    await leave_consult_mode(state)
    log_event(logger, action="ai.consult", stage="exit", result="ok")
    await send_welcome(message, lang, i18n)


@router.message(StateFilter(ConsultForm.active))
async def consult_message(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    ai: AIConsultantService,
    catalog: Catalog,
) -> None:
    lang = await current_lang(message, langs)
    if not lang:
        from excursion_ai.handlers.start import send_language_prompt

        await send_language_prompt(message, i18n)
        return
    if not message.from_user or not message.text:
        return

    # Меню и язык обрабатываются другими роутерами раньше по порядку.
    # Сюда попадают только тексты внутри ConsultForm.active без MenuText-совпадений
    # из этого роутера; пункты главного меню зарегистрированы в других роутерах.
    if _is_non_question_text(message.text, i18n):
        return

    if not ai.is_available():
        missing = ", ".join(ai.missing_settings) or "AI"
        await message.answer(
            i18n.t(lang, "ai.unavailable", missing=missing),
            reply_markup=consult_keyboard(lang, i18n),
        )
        return

    waiting = await message.answer(i18n.t(lang, "ai.thinking"))
    try:
        result = await ai.ask(
            telegram_user_id=message.from_user.id,
            interface_lang=lang,
            user_text=message.text,
        )
    except AIBusyError:
        await waiting.edit_text(i18n.t(lang, "ai.error.busy"))
        await message.answer(
            i18n.t(lang, "ai.open_catalog_hint"),
            reply_markup=consult_keyboard(lang, i18n),
        )
        return
    except AILimitError as error:
        key = "ai.error.user_limit" if error.code == "user_limit" else "ai.error.global_limit"
        await waiting.edit_text(i18n.t(lang, key))
        await message.answer(
            i18n.t(lang, "ai.open_catalog_hint"),
            reply_markup=consult_keyboard(lang, i18n),
        )
        return
    except AIValidationError as error:
        key = {
            "input_too_long": "ai.error.input_too_long",
            "empty_input": "ai.error.empty_input",
            "invalid_response": "ai.error.invalid_response",
            "empty_answer": "ai.error.invalid_response",
        }.get(error.code, "ai.error.generic")
        await waiting.edit_text(i18n.t(lang, key))
        await message.answer(
            i18n.t(lang, "ai.open_catalog_hint"),
            reply_markup=consult_keyboard(lang, i18n),
        )
        return
    except (AIUnavailableError, AIConfigError):
        missing = ", ".join(ai.missing_settings) or "AI"
        await waiting.edit_text(i18n.t(lang, "ai.unavailable", missing=missing))
        await message.answer(
            i18n.t(lang, "ai.open_catalog_hint"),
            reply_markup=consult_keyboard(lang, i18n),
        )
        return
    except AIError as error:
        key = {
            "auth": "ai.error.auth",
            "permission": "ai.error.auth",
            "rate_limit": "ai.error.rate_limit",
            "timeout": "ai.error.timeout",
            "connection": "ai.error.unavailable_model",
            "model_unavailable": "ai.error.unavailable_model",
            "bad_request": "ai.error.generic",
            "api_error": "ai.error.generic",
        }.get(error.code, "ai.error.generic")
        log_event(
            logger,
            action="ai.consult",
            stage="failed",
            result="error",
            level=logging.ERROR,
            error_code=error.code,
        )
        await waiting.edit_text(i18n.t(lang, key))
        await message.answer(
            i18n.t(lang, "ai.open_catalog_hint"),
            reply_markup=consult_keyboard(lang, i18n),
        )
        return

    try:
        await waiting.edit_text(result.answer)
    except Exception:
        await message.answer(result.answer, reply_markup=consult_keyboard(lang, i18n))

    for excursion in result.recommended:
        await _send_recommendation(message, excursion=excursion, lang=lang, i18n=i18n)

    await message.answer(
        i18n.t(lang, "ai.continue_hint"),
        reply_markup=consult_keyboard(lang, i18n),
    )


async def _send_recommendation(
    message: Message,
    *,
    excursion: Excursion,
    lang: str,
    i18n: I18n,
) -> None:
    await message.answer(
        format_recommend_card(excursion, lang, i18n),
        reply_markup=excursion_recommend_keyboard(lang, i18n, excursion.id),
        parse_mode="HTML",
    )
