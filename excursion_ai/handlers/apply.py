from __future__ import annotations

import logging
import uuid
from datetime import date, datetime

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from excursion_ai.catalog import Catalog
from excursion_ai.config import Settings
from excursion_ai.crm.sync import ApplicationCrmSyncService
from excursion_ai.db.applications import ApplicationRepository
from excursion_ai.db.public_number import format_public_number
from excursion_ai.db.sessions import SessionRepository, format_rome_time
from excursion_ai.db.users import UserRepository
from excursion_ai.ai.service import AIConsultantService
from excursion_ai.forms.states import ApplyForm
from excursion_ai.forms.texts import validation_message
from excursion_ai.forms.validation import (
    FormValidationError,
    ROME_TZ,
    format_date_hint,
    parse_name,
    parse_participants,
    parse_phone,
)
from excursion_ai.handlers.apply_flow import (
    ask_conflict,
    cancel_form,
    clear_form,
    is_in_form,
    prompt_session_dates,
    prompt_session_times,
    refresh_offer_from_catalog,
    resume_current_step,
    save_application,
    send_summary,
    start_application_form,
)
from excursion_ai.handlers.common import LANGUAGE_CODES, CancelText, all_menu_texts, current_lang
from excursion_ai.handlers.start import send_welcome
from excursion_ai.i18n import I18n
from excursion_ai.keyboards import (
    ApplyCallback,
    ExcursionCallback,
    SchedulePickCallback,
    all_cancel_texts,
    all_consult_nav_texts,
    all_skip_phone_texts,
    cancel_keyboard,
    confirm_keyboard,
    edit_field_keyboard,
    main_menu,
    phone_keyboard,
)
from excursion_ai.languages import LanguageStore
from excursion_ai.log_context import get_event_id, log_event, log_exception

router = Router(name="apply")
logger = logging.getLogger(__name__)


def _is_reserved_text(text: str, i18n: I18n) -> bool:
    reserved = set(LANGUAGE_CODES)
    reserved |= all_menu_texts(i18n)
    reserved |= all_cancel_texts(i18n)
    reserved |= all_skip_phone_texts(i18n)
    reserved |= all_consult_nav_texts(i18n)
    return text in reserved


async def _lang_or_prompt(
    message: Message,
    langs: LanguageStore,
    i18n: I18n,
) -> str | None:
    lang = await current_lang(message, langs)
    if lang:
        return lang
    from excursion_ai.handlers.start import send_language_prompt

    await send_language_prompt(message, i18n)
    return None


@router.message(Command("cancel"))
async def cmd_cancel(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
) -> None:
    lang = await _lang_or_prompt(message, langs, i18n)
    if not lang:
        return
    if not await is_in_form(state):
        await message.answer(i18n.t(lang, "error.unknown"), reply_markup=main_menu(lang, i18n))
        return
    await cancel_form(message, state=state, lang=lang, i18n=i18n)


@router.message(
    StateFilter(
        ApplyForm.date,
        ApplyForm.participants,
        ApplyForm.name,
        ApplyForm.phone,
        ApplyForm.confirm,
        ApplyForm.edit_choice,
        ApplyForm.conflict,
    ),
    CancelText(),
)
async def reply_cancel(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
) -> None:
    lang = await _lang_or_prompt(message, langs, i18n)
    if not lang:
        return
    await cancel_form(message, state=state, lang=lang, i18n=i18n)


@router.callback_query(ApplyCallback.filter(F.action == "cancel"))
async def callback_cancel(
    callback: CallbackQuery,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
) -> None:
    if not callback.from_user or not isinstance(callback.message, Message):
        await callback.answer()
        return
    lang = await langs.get(callback.from_user.id) or "ru"
    await callback.answer()
    await cancel_form(callback.message, state=state, lang=lang, i18n=i18n)


@router.callback_query(ExcursionCallback.filter(F.action == "apply"))
async def callback_start_apply(
    callback: CallbackQuery,
    callback_data: ExcursionCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    catalog: Catalog,
) -> None:
    if not callback.from_user or not isinstance(callback.message, Message):
        await callback.answer()
        return
    lang = await langs.get(callback.from_user.id)
    if not lang:
        await callback.answer(i18n.t("ru", "error.choose_language"), show_alert=True)
        return

    current = await state.get_state()
    data = await state.get_data()
    active = current in {
        ApplyForm.date.state,
        ApplyForm.participants.state,
        ApplyForm.name.state,
        ApplyForm.phone.state,
        ApplyForm.confirm.state,
        ApplyForm.edit_choice.state,
    }
    if active and data.get("submission_key"):
        await callback.answer()
        await ask_conflict(
            callback.message,
            lang=lang,
            i18n=i18n,
            state=state,
            pending_action="start_apply",
            pending_excursion_id=callback_data.excursion_id,
        )
        return

    await callback.answer()
    await start_application_form(
        callback.message,
        state=state,
        lang=lang,
        i18n=i18n,
        catalog=catalog,
        excursion_id=callback_data.excursion_id,
    )


@router.callback_query(ApplyCallback.filter(F.action == "continue"))
async def callback_continue(
    callback: CallbackQuery,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    catalog: Catalog,
) -> None:
    if not callback.from_user or not isinstance(callback.message, Message):
        await callback.answer()
        return
    lang = await langs.get(callback.from_user.id) or "ru"
    data = await state.get_data()
    resume = data.get("resume_state") or ApplyForm.date.state
    await state.set_state(resume)
    data.pop("pending_action", None)
    data.pop("pending_excursion_id", None)
    data.pop("resume_state", None)
    await state.set_data(data)
    await callback.answer()
    log_event(logger, action="application.form", stage="continue", result="ok")
    await resume_current_step(
        callback.message,
        state=state,
        lang=lang,
        i18n=i18n,
        catalog=catalog,
    )


@router.callback_query(ApplyCallback.filter(F.action == "discard"))
async def callback_discard(
    callback: CallbackQuery,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    catalog: Catalog,
    applications: ApplicationRepository,
    ai: AIConsultantService,
) -> None:
    if not callback.from_user or not isinstance(callback.message, Message):
        await callback.answer()
        return
    lang = await langs.get(callback.from_user.id) or "ru"
    data = await state.get_data()
    pending_action = data.get("pending_action")
    pending_excursion_id = str(data.get("pending_excursion_id") or "")
    await callback.answer()
    await clear_form(state)
    log_event(logger, action="application.form", stage="discard", result="ok")
    await callback.message.answer(
        i18n.t(lang, "apply.cancelled"),
        reply_markup=main_menu(lang, i18n),
    )
    if pending_action == "start_apply" and pending_excursion_id:
        await start_application_form(
            callback.message,
            state=state,
            lang=lang,
            i18n=i18n,
            catalog=catalog,
            excursion_id=pending_excursion_id,
        )
    elif pending_action == "menu_excursions":
        from excursion_ai.handlers.catalog import send_catalog_list

        await send_catalog_list(callback.message, lang, i18n, catalog)
    elif pending_action == "menu_my_applications":
        from excursion_ai.handlers.my_applications import send_my_applications_page

        await send_my_applications_page(
            callback.message,
            lang=lang,
            i18n=i18n,
            applications=applications,
            page=0,
            telegram_user_id=callback.from_user.id,
        )
    elif pending_action == "menu_about":
        about_key = "about.text_ai_ready" if ai.is_available() else "about.text"
        await callback.message.answer(
            i18n.t(lang, about_key),
            reply_markup=main_menu(lang, i18n),
        )
    elif pending_action == "menu_start":
        await send_welcome(callback.message, lang, i18n)
    elif pending_action == "menu_ask_ai":
        from excursion_ai.handlers.consult import enter_consult_mode

        await enter_consult_mode(
            callback.message,
            state=state,
            lang=lang,
            i18n=i18n,
            ai=ai,
        )
    elif pending_action == "menu_admin":
        from excursion_ai.handlers.admin import open_admin_home

        await open_admin_home(
            callback.message,
            state=state,
            lang=lang,
            i18n=i18n,
        )


@router.message(StateFilter(ApplyForm.participants))
async def process_participants(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    sessions: SessionRepository,
) -> None:
    lang = await _lang_or_prompt(message, langs, i18n)
    if not lang or not message.text:
        return
    if message.text in all_cancel_texts(i18n):
        await cancel_form(message, state=state, lang=lang, i18n=i18n)
        return
    if _is_reserved_text(message.text, i18n):
        await message.answer(i18n.t(lang, "apply.error.menu_as_input"))
        return
    try:
        value = parse_participants(message.text)
    except FormValidationError as error:
        log_event(
            logger,
            action="application.form",
            stage="validate_participants",
            result="error",
            code=error.code,
        )
        await message.answer(validation_message(lang, i18n, error.code))
        await message.answer(
            i18n.t(lang, "apply.ask_participants"),
            reply_markup=cancel_keyboard(lang, i18n),
        )
        return

    data = await state.get_data()
    data["participants_count"] = value
    # Смена числа участников сбрасывает выбранный сеанс.
    data.pop("session_id", None)
    data.pop("session_starts_at", None)
    data.pop("requested_date", None)
    return_to_confirm = bool(data.get("return_to_confirm"))
    data["return_to_confirm"] = False
    await state.set_data(data)
    log_event(logger, action="application.form", stage="participants", result="ok")
    await prompt_session_dates(
        message, state=state, lang=lang, i18n=i18n, sessions=sessions
    )
    if return_to_confirm:
        # После выбора даты/времени вернёмся к summary через time handler.
        data = await state.get_data()
        data["return_to_confirm"] = True
        await state.set_data(data)


@router.callback_query(SchedulePickCallback.filter())
async def callback_schedule_pick(
    callback: CallbackQuery,
    callback_data: SchedulePickCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    sessions: SessionRepository,
) -> None:
    if not callback.from_user or not isinstance(callback.message, Message):
        await callback.answer()
        return
    lang = await langs.get(callback.from_user.id) or "ru"
    data = await state.get_data()
    if not data.get("submission_key") or not data.get("participants_count"):
        await callback.answer(i18n.t(lang, "apply.stale"), show_alert=True)
        return

    if callback_data.action == "back_dates":
        await callback.answer()
        await prompt_session_dates(
            callback.message, state=state, lang=lang, i18n=i18n, sessions=sessions
        )
        return

    if callback_data.action == "date":
        try:
            day = datetime.strptime(callback_data.day, "%d.%m.%Y").date()
        except ValueError:
            await callback.answer(i18n.t(lang, "apply.error.date_format"), show_alert=True)
            return
        data["requested_date"] = day.isoformat()
        data.pop("session_id", None)
        data.pop("session_starts_at", None)
        await state.set_data(data)
        await callback.answer()
        await prompt_session_times(
            callback.message,
            state=state,
            lang=lang,
            i18n=i18n,
            sessions=sessions,
            day=day,
        )
        return

    if callback_data.action == "time":
        try:
            session_id = uuid.UUID(callback_data.session_id)
        except ValueError:
            await callback.answer(i18n.t(lang, "apply.stale"), show_alert=True)
            return
        availability = await sessions.availability_for(session_id)
        participants = int(data["participants_count"])
        if (
            availability is None
            or not availability.session.is_active
            or availability.free < participants
            or availability.session.excursion_id != str(data.get("excursion_id"))
        ):
            await callback.answer(i18n.t(lang, "apply.session_full"), show_alert=True)
            await prompt_session_dates(
                callback.message, state=state, lang=lang, i18n=i18n, sessions=sessions
            )
            return
        starts = availability.session.starts_at
        data["session_id"] = str(session_id)
        data["session_starts_at"] = starts.isoformat()
        data["requested_date"] = starts.astimezone(ROME_TZ).date().isoformat()
        return_to_confirm = bool(data.get("return_to_confirm"))
        data["return_to_confirm"] = False
        await state.set_data(data)
        await callback.answer()
        log_event(logger, action="application.form", stage="time", result="ok")
        if return_to_confirm:
            await send_summary(callback.message, state=state, lang=lang, i18n=i18n)
            return
        await state.set_state(ApplyForm.name)
        await callback.message.answer(
            i18n.t(lang, "apply.ask_name"),
            reply_markup=cancel_keyboard(lang, i18n),
        )
        return

    await callback.answer()


@router.message(StateFilter(ApplyForm.date))
async def process_date_text_fallback(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    sessions: SessionRepository,
) -> None:
    lang = await _lang_or_prompt(message, langs, i18n)
    if not lang or not message.text:
        return
    if message.text in all_cancel_texts(i18n):
        await cancel_form(message, state=state, lang=lang, i18n=i18n)
        return
    await message.answer(i18n.t(lang, "apply.use_date_buttons"))
    await prompt_session_dates(
        message, state=state, lang=lang, i18n=i18n, sessions=sessions
    )


@router.message(StateFilter(ApplyForm.time))
async def process_time_text_fallback(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    sessions: SessionRepository,
) -> None:
    lang = await _lang_or_prompt(message, langs, i18n)
    if not lang or not message.text:
        return
    if message.text in all_cancel_texts(i18n):
        await cancel_form(message, state=state, lang=lang, i18n=i18n)
        return
    await message.answer(i18n.t(lang, "apply.use_time_buttons"))
    data = await state.get_data()
    if data.get("requested_date"):
        await prompt_session_times(
            message,
            state=state,
            lang=lang,
            i18n=i18n,
            sessions=sessions,
            day=date.fromisoformat(str(data["requested_date"])),
        )
    else:
        await prompt_session_dates(
            message, state=state, lang=lang, i18n=i18n, sessions=sessions
        )


@router.message(StateFilter(ApplyForm.name))
async def process_name(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
) -> None:
    lang = await _lang_or_prompt(message, langs, i18n)
    if not lang or not message.text:
        return
    if message.text in all_cancel_texts(i18n):
        await cancel_form(message, state=state, lang=lang, i18n=i18n)
        return
    if _is_reserved_text(message.text, i18n):
        await message.answer(i18n.t(lang, "apply.error.menu_as_input"))
        return
    try:
        value = parse_name(message.text)
    except FormValidationError as error:
        log_event(
            logger,
            action="application.form",
            stage="validate_name",
            result="error",
            code=error.code,
        )
        await message.answer(validation_message(lang, i18n, error.code))
        await message.answer(
            i18n.t(lang, "apply.ask_name"),
            reply_markup=cancel_keyboard(lang, i18n),
        )
        return

    data = await state.get_data()
    data["customer_name"] = value
    return_to_confirm = bool(data.get("return_to_confirm"))
    data["return_to_confirm"] = False
    await state.set_data(data)
    log_event(logger, action="application.form", stage="name", result="ok")
    if return_to_confirm:
        await send_summary(message, state=state, lang=lang, i18n=i18n)
        return
    await state.set_state(ApplyForm.phone)
    await message.answer(
        i18n.t(lang, "apply.ask_phone"),
        reply_markup=phone_keyboard(lang, i18n),
    )


@router.message(StateFilter(ApplyForm.phone))
async def process_phone(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
) -> None:
    lang = await _lang_or_prompt(message, langs, i18n)
    if not lang or not message.text:
        return
    if message.text in all_cancel_texts(i18n):
        await cancel_form(message, state=state, lang=lang, i18n=i18n)
        return
    if message.text in all_skip_phone_texts(i18n):
        phone_value = None
        log_event(logger, action="application.form", stage="phone", result="skipped")
    elif _is_reserved_text(message.text, i18n):
        await message.answer(i18n.t(lang, "apply.error.menu_as_input"))
        return
    else:
        try:
            phone_value = parse_phone(message.text)
        except FormValidationError as error:
            log_event(
                logger,
                action="application.form",
                stage="validate_phone",
                result="error",
                code=error.code,
            )
            await message.answer(validation_message(lang, i18n, error.code))
            await message.answer(
                i18n.t(lang, "apply.ask_phone"),
                reply_markup=phone_keyboard(lang, i18n),
            )
            return
        log_event(logger, action="application.form", stage="phone", result="ok")

    data = await state.get_data()
    data["phone"] = phone_value
    data["return_to_confirm"] = False
    await state.set_data(data)
    await send_summary(message, state=state, lang=lang, i18n=i18n)


@router.callback_query(ApplyCallback.filter(F.action == "edit"))
async def callback_edit(
    callback: CallbackQuery,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
) -> None:
    if not callback.from_user or not isinstance(callback.message, Message):
        await callback.answer()
        return
    lang = await langs.get(callback.from_user.id) or "ru"
    if await state.get_state() != ApplyForm.confirm.state:
        await callback.answer(i18n.t(lang, "apply.stale"), show_alert=True)
        return
    await state.set_state(ApplyForm.edit_choice)
    await callback.answer()
    await callback.message.answer(
        i18n.t(lang, "apply.edit_choose"),
        reply_markup=edit_field_keyboard(lang, i18n),
    )


@router.callback_query(ApplyCallback.filter(F.action == "edit_field"))
async def callback_edit_field(
    callback: CallbackQuery,
    callback_data: ApplyCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    sessions: SessionRepository,
) -> None:
    if not callback.from_user or not isinstance(callback.message, Message):
        await callback.answer()
        return
    lang = await langs.get(callback.from_user.id) or "ru"
    data = await state.get_data()
    if not data.get("submission_key"):
        await callback.answer(i18n.t(lang, "apply.stale"), show_alert=True)
        await clear_form(state)
        await callback.message.answer(
            i18n.t(lang, "apply.stale"),
            reply_markup=main_menu(lang, i18n),
        )
        return

    data["return_to_confirm"] = True
    await state.set_data(data)
    field = callback_data.field
    await callback.answer()
    log_event(
        logger,
        action="application.form",
        stage="edit_field",
        result="ok",
        field=field,
    )
    if field == "date":
        await prompt_session_dates(
            callback.message,
            state=state,
            lang=lang,
            i18n=i18n,
            sessions=sessions,
        )
    elif field == "participants":
        await state.set_state(ApplyForm.participants)
        await callback.message.answer(
            i18n.t(lang, "apply.ask_participants"),
            reply_markup=cancel_keyboard(lang, i18n),
        )
    elif field == "name":
        await state.set_state(ApplyForm.name)
        await callback.message.answer(
            i18n.t(lang, "apply.ask_name"),
            reply_markup=cancel_keyboard(lang, i18n),
        )
    elif field == "phone":
        await state.set_state(ApplyForm.phone)
        await callback.message.answer(
            i18n.t(lang, "apply.ask_phone"),
            reply_markup=phone_keyboard(lang, i18n),
        )


@router.callback_query(ApplyCallback.filter(F.action == "confirm"))
async def callback_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    applications: ApplicationRepository,
    users: UserRepository,
    catalog: Catalog,
    settings: Settings,
    crm_sync: ApplicationCrmSyncService | None = None,
) -> None:
    if not callback.from_user or not isinstance(callback.message, Message):
        await callback.answer()
        return
    lang = await langs.get(callback.from_user.id) or "ru"
    data = await state.get_data()
    if await state.get_state() != ApplyForm.confirm.state or not data.get("submission_key"):
        await callback.answer(i18n.t(lang, "apply.stale"), show_alert=True)
        await clear_form(state)
        await callback.message.answer(
            i18n.t(lang, "apply.stale"),
            reply_markup=main_menu(lang, i18n),
        )
        return

    await callback.answer()
    offer_status = await refresh_offer_from_catalog(state, catalog=catalog, lang=lang)
    if offer_status == "unavailable":
        await clear_form(state)
        await callback.message.answer(
            i18n.t(lang, "apply.unavailable"),
            reply_markup=main_menu(lang, i18n),
        )
        return
    if offer_status == "price_changed":
        await callback.message.answer(i18n.t(lang, "apply.price_changed"))
        await send_summary(callback.message, state=state, lang=lang, i18n=i18n)
        return

    crm_ready = bool(crm_sync and crm_sync.is_available())
    try:
        application = await save_application(
            telegram_user_id=callback.from_user.id,
            state=state,
            users=users,
            applications=applications,
            lang=lang,
            crm_ready=crm_ready,
        )
    except Exception as error:
        error_id = get_event_id()
        log_exception(
            logger,
            action="application.save",
            stage="failed",
            exc=error,
            result="error",
            error_id=error_id,
        )
        await callback.message.answer(
            i18n.t(lang, "apply.db_error"),
            reply_markup=confirm_keyboard(lang, i18n),
        )
        return

    await callback.message.answer(
        i18n.t(
            lang,
            "apply.success",
            application_id=format_public_number(int(application.public_number)),
            title=application.excursion_title,
            date=format_date_hint(application.requested_date),
            time=(
                format_rome_time(application.session_starts_at)
                if application.session_starts_at
                else "—"
            ),
            count=application.participants_count,
        ),
        reply_markup=main_menu(lang, i18n),
    )
    await clear_form(state)
    # CRM только после успешного ответа пользователю; сбой CRM заявку не трогает.
    if crm_sync is not None:
        crm_sync.schedule_sync(application.id)
