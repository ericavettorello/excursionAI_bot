"""Админ-управление расписанием сеансов экскурсий (меню и визард создания)."""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from excursion_ai.config import Settings
from excursion_ai.db.excursions import ExcursionRepository
from excursion_ai.db.sessions import (
    SessionCapacityError,
    SessionRepository,
    SessionValidationError,
    combine_rome,
    format_rome_datetime,
    parse_rome_time,
)
from excursion_ai.forms.admin_states import AdminExcursionForm
from excursion_ai.forms.states import AdminScheduleForm
from excursion_ai.forms.validation import ROME_TZ
from excursion_ai.handlers.admin_access import require_admin
from excursion_ai.i18n import I18n
from excursion_ai.handlers.common import LEGACY_CANCEL
from excursion_ai.keyboards import (
    SCHEDULE_PAGE_SIZE,
    AdminScheduleCallback,
    admin_schedule_error_keyboard,
    admin_schedule_excursions_keyboard,
    admin_schedule_linked_keyboard,
    admin_schedule_list_keyboard,
    admin_schedule_nav_keyboard,
    admin_session_card_keyboard,
    cancel_keyboard,
    remove_keyboard,
)
from excursion_ai.languages import LanguageStore
from excursion_ai.log_context import log_event

router = Router(name="admin_schedule")
logger = logging.getLogger(__name__)

# schedule_return: menu | wizard | summary | card
RETURN_MENU = "menu"
RETURN_WIZARD = "wizard"
RETURN_SUMMARY = "summary"
RETURN_CARD = "card"

def _is_cancel_text(text: str, *, lang: str, i18n: I18n) -> bool:
    value = text.strip()
    if value == i18n.t(lang, "apply.cancel"):
        return True
    return value in LEGACY_CANCEL


async def _resolve_excursion_id(
    *,
    state: FSMContext,
    sessions: SessionRepository,
    callback_data: AdminScheduleCallback,
) -> str:
    """excursion_id из callback, FSM или записи сеанса (callback часто без excursion_id)."""
    if callback_data.excursion_id:
        return callback_data.excursion_id
    data = await state.get_data()
    from_state = str(
        data.get("schedule_excursion_id") or data.get("excursion_id") or ""
    )
    if from_state:
        return from_state
    if callback_data.session_id:
        row = await sessions.get_by_id(uuid.UUID(callback_data.session_id))
        if row is not None:
            return row.excursion_id
    return ""


async def open_schedule_for_excursion(
    message: Message,
    *,
    state: FSMContext,
    lang: str,
    i18n: I18n,
    sessions: SessionRepository,
    excursion_id: str,
    schedule_return: str = RETURN_WIZARD,
    notice: str = "",
) -> None:
    """Открывает список сеансов для экскурсии (визард / карточка / меню)."""
    await state.update_data(
        schedule_excursion_id=excursion_id,
        excursion_id=excursion_id,
        schedule_return=schedule_return,
        wizard=schedule_return == RETURN_WIZARD,
    )
    await _send_session_list(
        message,
        state=state,
        lang=lang,
        i18n=i18n,
        sessions=sessions,
        excursion_id=excursion_id,
        notice=notice,
    )


async def _schedule_mode(state: FSMContext) -> str:
    data = await state.get_data()
    return str(data.get("schedule_return") or RETURN_MENU)


def _format_session_list_lines(
    rows: list,
    *,
    lang: str,
    i18n: I18n,
) -> list[str]:
    """Полный список сеансов (прошлые и будущие); несколько времён — отдельные строки."""
    lines: list[str] = []
    for row in rows:
        status = i18n.t(
            lang,
            "admin.schedule.active_yes" if row.is_active else "admin.schedule.active_no",
        )
        lines.append(
            i18n.t(
                lang,
                "admin.schedule.list_item_full",
                when=format_rome_datetime(row.starts_at),
                capacity=row.capacity,
                status=status,
            )
        )
    return lines


async def _send_session_list(
    message: Message,
    *,
    state: FSMContext,
    lang: str,
    i18n: I18n,
    sessions: SessionRepository,
    excursion_id: str,
    notice: str = "",
    page: int | None = None,
) -> None:
    from excursion_ai.log_context import get_event_id, log_exception

    try:
        rows = await sessions.list_for_excursion(excursion_id, include_inactive=True)
        data = await state.get_data()
        current_page = int(data.get("schedule_page") or 0) if page is None else page
        pages = max(1, (len(rows) + SCHEDULE_PAGE_SIZE - 1) // SCHEDULE_PAGE_SIZE)
        current_page = max(0, min(current_page, pages - 1))
        slice_rows = rows[
            current_page * SCHEDULE_PAGE_SIZE : (current_page + 1) * SCHEDULE_PAGE_SIZE
        ]
        labels = [
            (
                str(row.id),
                f"{format_rome_datetime(row.starts_at)} · "
                f"{i18n.t(lang, 'admin.schedule.capacity_short', capacity=row.capacity)}",
                bool(row.is_active),
            )
            for row in slice_rows
        ]
        mode = await _schedule_mode(state)
        await state.update_data(
            schedule_day=None,
            schedule_time=None,
            schedule_session_id=None,
            schedule_page=current_page,
            schedule_excursion_id=excursion_id,
            excursion_id=excursion_id,
        )
        await state.set_state(AdminScheduleForm.list_sessions)
        lines: list[str] = []
        if notice:
            lines.append(notice)
            lines.append("")
        if mode == RETURN_WIZARD:
            lines.append(i18n.t(lang, "admin.schedule.wizard_title"))
            if rows:
                lines.append(i18n.t(lang, "admin.schedule.wizard_list_header"))
                lines.extend(_format_session_list_lines(slice_rows, lang=lang, i18n=i18n))
            else:
                lines.append(i18n.t(lang, "admin.schedule.wizard_empty"))
            lines.append("")
            lines.append(i18n.t(lang, "admin.schedule.wizard_hint"))
        else:
            lines.append(i18n.t(lang, "admin.schedule.list_title"))
            if rows:
                lines.extend(_format_session_list_lines(slice_rows, lang=lang, i18n=i18n))
            else:
                lines.append(i18n.t(lang, "admin.schedule.wizard_empty"))
            if rows:
                lines.append("")
                lines.append(i18n.t(lang, "admin.schedule.multi_hint"))
        if pages > 1:
            lines.append("")
            lines.append(
                i18n.t(
                    lang,
                    "admin.schedule.page",
                    page=current_page + 1,
                    pages=pages,
                )
            )
        await message.answer("\n".join(lines), reply_markup=remove_keyboard())
        await message.answer(
            i18n.t(lang, "admin.schedule.choose_action"),
            reply_markup=admin_schedule_list_keyboard(
                lang,
                i18n,
                excursion_id=excursion_id,
                sessions=labels,
                mode=mode,
                page=current_page,
                pages=pages,
            ),
        )
    except Exception as error:
        error_id = get_event_id()
        log_exception(
            logger,
            action="admin.schedule",
            stage="list",
            exc=error,
            result="error",
            error_id=error_id,
            excursion_id=excursion_id,
        )
        await message.answer(
            i18n.t(lang, "admin.schedule.tech_error", error_id=error_id),
            reply_markup=admin_schedule_error_keyboard(lang, i18n),
        )


async def _prompt_with_nav(
    message: Message,
    *,
    lang: str,
    i18n: I18n,
    text_key: str,
) -> None:
    await message.answer(
        i18n.t(lang, text_key),
        reply_markup=cancel_keyboard(lang, i18n),
    )
    await message.answer(
        i18n.t(lang, "admin.schedule.nav_hint"),
        reply_markup=admin_schedule_nav_keyboard(lang, i18n),
    )


async def _finish_schedule(
    message: Message,
    *,
    state: FSMContext,
    lang: str,
    i18n: I18n,
    excursions: ExcursionRepository,
    excursion_id: str,
) -> None:
    mode = await _schedule_mode(state)
    from excursion_ai.handlers import admin as admin_handlers

    if mode == RETURN_CARD:
        await state.clear()
        await admin_handlers._send_card(
            message,
            lang=lang,
            i18n=i18n,
            excursions=excursions,
            excursion_id=excursion_id,
            notice=i18n.t(lang, "admin.schedule.saved"),
        )
        return
    # wizard / summary → сводка
    excursion = await excursions.get(excursion_id)
    if excursion is None:
        await state.clear()
        await message.answer(i18n.t(lang, "admin.not_found"))
        return
    await admin_handlers._bind_wizard_excursion(state, excursion, wizard=True)
    await state.set_state(AdminExcursionForm.preview)
    await admin_handlers._send_summary(
        message,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        excursion_id=excursion_id,
        notice=i18n.t(lang, "admin.schedule.saved"),
    )


@router.callback_query(AdminScheduleCallback.filter(F.action == "home"))
async def schedule_home(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    i18n: I18n,
    langs: LanguageStore,
    excursions: ExcursionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await state.set_state(AdminScheduleForm.pick_excursion)
    await state.update_data(schedule_return=RETURN_MENU)
    published = await excursions.list_by_status("published")
    drafts = await excursions.list_by_status("draft")
    items = [(item.id, item.title(lang)) for item in [*published, *drafts]]
    await callback.answer()
    await callback.message.answer(
        i18n.t(lang, "admin.schedule.pick_excursion"),
        reply_markup=admin_schedule_excursions_keyboard(lang, i18n, items),
    )


@router.callback_query(AdminScheduleCallback.filter(F.action.in_({"excursion", "open"})))
async def schedule_list(
    callback: CallbackQuery,
    callback_data: AdminScheduleCallback,
    state: FSMContext,
    settings: Settings,
    i18n: I18n,
    langs: LanguageStore,
    sessions: SessionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    data = await state.get_data()
    schedule_return = str(
        callback_data.ret
        or data.get("schedule_return")
        or (RETURN_SUMMARY if callback_data.action == "open" else RETURN_MENU)
    )
    await callback.answer()
    await open_schedule_for_excursion(
        callback.message,
        state=state,
        lang=lang,
        i18n=i18n,
        sessions=sessions,
        excursion_id=callback_data.excursion_id,
        schedule_return=schedule_return,
    )


@router.callback_query(AdminScheduleCallback.filter(F.action.in_({"page_next", "page_prev"})))
async def schedule_page(
    callback: CallbackQuery,
    callback_data: AdminScheduleCallback,
    state: FSMContext,
    settings: Settings,
    i18n: I18n,
    langs: LanguageStore,
    sessions: SessionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    current = await state.get_state()
    if current != AdminScheduleForm.list_sessions.state:
        await callback.answer(i18n.t(lang, "admin.stale_action"), show_alert=True)
        return
    data = await state.get_data()
    excursion_id = str(data.get("schedule_excursion_id") or data.get("excursion_id") or "")
    if not excursion_id:
        await callback.answer(i18n.t(lang, "admin.stale_action"), show_alert=True)
        return
    page = int(data.get("schedule_page") or 0)
    page = page + 1 if callback_data.action == "page_next" else page - 1
    await callback.answer()
    await _send_session_list(
        callback.message,
        state=state,
        lang=lang,
        i18n=i18n,
        sessions=sessions,
        excursion_id=excursion_id,
        page=page,
    )


@router.callback_query(AdminScheduleCallback.filter(F.action == "nav_back"))
async def schedule_nav_back(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    i18n: I18n,
    langs: LanguageStore,
    sessions: SessionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    data = await state.get_data()
    excursion_id = str(data.get("schedule_excursion_id") or data.get("excursion_id") or "")
    current = await state.get_state()
    await callback.answer()
    if current == AdminScheduleForm.add_capacity.state:
        await state.set_state(AdminScheduleForm.add_time)
        await _prompt_with_nav(
            callback.message, lang=lang, i18n=i18n, text_key="admin.schedule.ask_time"
        )
        return
    if current == AdminScheduleForm.add_time.state:
        await state.set_state(AdminScheduleForm.add_date)
        await _prompt_with_nav(
            callback.message, lang=lang, i18n=i18n, text_key="admin.schedule.ask_date"
        )
        return
    if current in {
        AdminScheduleForm.add_date.state,
        AdminScheduleForm.edit_capacity.state,
        AdminScheduleForm.edit_time.state,
        AdminScheduleForm.edit_date.state,
    }:
        if not excursion_id:
            await callback.message.answer(i18n.t(lang, "admin.stale_action"))
            return
        await _send_session_list(
            callback.message,
            state=state,
            lang=lang,
            i18n=i18n,
            sessions=sessions,
            excursion_id=excursion_id,
            notice=i18n.t(lang, "admin.schedule.input_cancelled"),
        )
        return
    if not excursion_id:
        await callback.message.answer(i18n.t(lang, "admin.stale_action"))
        return
    await _send_session_list(
        callback.message,
        state=state,
        lang=lang,
        i18n=i18n,
        sessions=sessions,
        excursion_id=excursion_id,
        notice=i18n.t(lang, "admin.schedule.input_cancelled"),
    )


@router.callback_query(AdminScheduleCallback.filter(F.action == "nav_cancel"))
async def schedule_nav_cancel(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    i18n: I18n,
    langs: LanguageStore,
    excursions: ExcursionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    data = await state.get_data()
    excursion_id = str(data.get("schedule_excursion_id") or data.get("excursion_id") or "")
    await callback.answer()
    if not excursion_id:
        await state.clear()
        await callback.message.answer(i18n.t(lang, "admin.cancelled"))
        return
    await _finish_schedule(
        callback.message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        excursion_id=excursion_id,
    )


@router.callback_query(AdminScheduleCallback.filter(F.action == "retry"))
async def schedule_retry(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    i18n: I18n,
    langs: LanguageStore,
    sessions: SessionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    data = await state.get_data()
    excursion_id = str(data.get("schedule_excursion_id") or data.get("excursion_id") or "")
    current = await state.get_state()
    await callback.answer()
    prompt_by_state = {
        AdminScheduleForm.add_date.state: "admin.schedule.ask_date",
        AdminScheduleForm.add_time.state: "admin.schedule.ask_time",
        AdminScheduleForm.add_capacity.state: "admin.schedule.ask_capacity",
        AdminScheduleForm.edit_date.state: "admin.schedule.ask_date",
        AdminScheduleForm.edit_time.state: "admin.schedule.ask_time",
        AdminScheduleForm.edit_capacity.state: "admin.schedule.ask_capacity",
    }
    text_key = prompt_by_state.get(current or "")
    if text_key:
        await _prompt_with_nav(
            callback.message, lang=lang, i18n=i18n, text_key=text_key
        )
        return
    if not excursion_id:
        await callback.message.answer(i18n.t(lang, "admin.stale_action"))
        return
    await _send_session_list(
        callback.message,
        state=state,
        lang=lang,
        i18n=i18n,
        sessions=sessions,
        excursion_id=excursion_id,
    )


@router.callback_query(AdminScheduleCallback.filter(F.action == "finish"))
async def schedule_finish(
    callback: CallbackQuery,
    callback_data: AdminScheduleCallback,
    state: FSMContext,
    settings: Settings,
    i18n: I18n,
    langs: LanguageStore,
    excursions: ExcursionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await callback.answer()
    await _finish_schedule(
        callback.message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        excursion_id=callback_data.excursion_id,
    )


@router.callback_query(AdminScheduleCallback.filter(F.action == "add"))
async def schedule_add_start(
    callback: CallbackQuery,
    callback_data: AdminScheduleCallback,
    state: FSMContext,
    settings: Settings,
    i18n: I18n,
    langs: LanguageStore,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await state.update_data(schedule_excursion_id=callback_data.excursion_id)
    await state.set_state(AdminScheduleForm.add_date)
    await callback.answer()
    await _prompt_with_nav(
        callback.message, lang=lang, i18n=i18n, text_key="admin.schedule.ask_date"
    )


async def _cancel_input_to_list(
    message: Message,
    *,
    state: FSMContext,
    lang: str,
    i18n: I18n,
    sessions: SessionRepository,
) -> bool:
    """True если обработана отмена ввода — возврат к списку без потери черновика."""
    if not message.text or not _is_cancel_text(message.text, lang=lang, i18n=i18n):
        return False
    data = await state.get_data()
    excursion_id = str(data.get("schedule_excursion_id") or data.get("excursion_id") or "")
    if not excursion_id:
        await message.answer(
            i18n.t(lang, "admin.schedule.not_found"),
            reply_markup=remove_keyboard(),
        )
        return True
    await _send_session_list(
        message,
        state=state,
        lang=lang,
        i18n=i18n,
        sessions=sessions,
        excursion_id=excursion_id,
        notice=i18n.t(lang, "admin.schedule.input_cancelled"),
    )
    return True


@router.message(StateFilter(AdminScheduleForm.add_date))
async def schedule_add_date(
    message: Message,
    state: FSMContext,
    settings: Settings,
    i18n: I18n,
    langs: LanguageStore,
    sessions: SessionRepository,
) -> None:
    lang = await require_admin(message, settings, i18n, langs)
    if not lang or not message.text:
        return
    if await _cancel_input_to_list(
        message, state=state, lang=lang, i18n=i18n, sessions=sessions
    ):
        return
    try:
        day = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
    except ValueError:
        await message.answer(i18n.t(lang, "admin.schedule.error_date"))
        await _prompt_with_nav(
            message, lang=lang, i18n=i18n, text_key="admin.schedule.ask_date"
        )
        return
    await state.update_data(schedule_day=day.isoformat())
    await state.set_state(AdminScheduleForm.add_time)
    await _prompt_with_nav(
        message, lang=lang, i18n=i18n, text_key="admin.schedule.ask_time"
    )


@router.message(StateFilter(AdminScheduleForm.add_time))
async def schedule_add_time(
    message: Message,
    state: FSMContext,
    settings: Settings,
    i18n: I18n,
    langs: LanguageStore,
    sessions: SessionRepository,
) -> None:
    lang = await require_admin(message, settings, i18n, langs)
    if not lang or not message.text:
        return
    if await _cancel_input_to_list(
        message, state=state, lang=lang, i18n=i18n, sessions=sessions
    ):
        return
    try:
        hhmm = parse_rome_time(message.text)
    except SessionValidationError:
        await message.answer(i18n.t(lang, "admin.schedule.error_time"))
        await _prompt_with_nav(
            message, lang=lang, i18n=i18n, text_key="admin.schedule.ask_time"
        )
        return
    await state.update_data(schedule_time=hhmm.strftime("%H:%M"))
    await state.set_state(AdminScheduleForm.add_capacity)
    await _prompt_with_nav(
        message, lang=lang, i18n=i18n, text_key="admin.schedule.ask_capacity"
    )


@router.message(StateFilter(AdminScheduleForm.add_capacity))
async def schedule_add_capacity(
    message: Message,
    state: FSMContext,
    settings: Settings,
    i18n: I18n,
    langs: LanguageStore,
    sessions: SessionRepository,
) -> None:
    lang = await require_admin(message, settings, i18n, langs)
    if not lang or not message.text:
        return
    if await _cancel_input_to_list(
        message, state=state, lang=lang, i18n=i18n, sessions=sessions
    ):
        return
    if not message.text.strip().isdigit() or int(message.text.strip()) <= 0:
        await message.answer(i18n.t(lang, "admin.schedule.error_capacity"))
        await _prompt_with_nav(
            message, lang=lang, i18n=i18n, text_key="admin.schedule.ask_capacity"
        )
        return
    data = await state.get_data()
    excursion_id = str(data.get("schedule_excursion_id") or data.get("excursion_id") or "")
    day = date.fromisoformat(str(data["schedule_day"]))
    hhmm = parse_rome_time(str(data["schedule_time"]))
    starts = combine_rome(day, hhmm)
    try:
        row = await sessions.create_session(
            excursion_id=excursion_id,
            starts_at=starts,
            capacity=int(message.text.strip()),
        )
    except SessionValidationError:
        # Дубликат даты+времени: сохраняем дату, повторяем только время.
        await state.set_state(AdminScheduleForm.add_time)
        await message.answer(i18n.t(lang, "admin.schedule.error_duplicate"))
        await _prompt_with_nav(
            message, lang=lang, i18n=i18n, text_key="admin.schedule.ask_time"
        )
        return
    except Exception as error:
        from excursion_ai.log_context import get_event_id, log_exception

        error_id = get_event_id()
        log_exception(
            logger,
            action="admin.schedule",
            stage="create",
            exc=error,
            result="error",
            error_id=error_id,
            excursion_id=excursion_id,
        )
        await message.answer(
            i18n.t(lang, "admin.schedule.tech_error", error_id=error_id),
            reply_markup=admin_schedule_error_keyboard(lang, i18n),
        )
        return
    log_event(
        logger,
        action="admin.schedule",
        stage="create",
        result="ok",
        session_id=str(row.id),
        excursion_id=excursion_id,
    )
    await _send_session_list(
        message,
        state=state,
        lang=lang,
        i18n=i18n,
        sessions=sessions,
        excursion_id=excursion_id,
        notice=i18n.t(
            lang,
            "admin.schedule.created",
            when=format_rome_datetime(row.starts_at),
            capacity=row.capacity,
        ),
    )


@router.callback_query(AdminScheduleCallback.filter(F.action == "view"))
async def schedule_view(
    callback: CallbackQuery,
    callback_data: AdminScheduleCallback,
    state: FSMContext,
    settings: Settings,
    i18n: I18n,
    langs: LanguageStore,
    sessions: SessionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    if not callback_data.session_id:
        await callback.answer(i18n.t(lang, "admin.stale_action"), show_alert=True)
        return
    row = await sessions.get_by_id(uuid.UUID(callback_data.session_id))
    if row is None:
        await callback.answer(i18n.t(lang, "admin.schedule.not_found"), show_alert=True)
        return
    occupied = await sessions.occupied_confirmed(row.id)
    linked = await sessions.count_applications(row.id)
    await state.update_data(
        schedule_session_id=str(row.id),
        schedule_excursion_id=row.excursion_id,
        excursion_id=row.excursion_id,
    )
    await callback.answer()
    text = i18n.t(
        lang,
        "admin.schedule.card",
        when=format_rome_datetime(row.starts_at),
        capacity=row.capacity,
        occupied=occupied,
        active=i18n.t(
            lang,
            "admin.schedule.active_yes" if row.is_active else "admin.schedule.active_no",
        ),
    )
    if linked:
        text += "\n" + i18n.t(lang, "admin.schedule.linked_apps", count=linked)
    await callback.message.answer(
        text,
        reply_markup=admin_session_card_keyboard(
            lang,
            i18n,
            excursion_id=row.excursion_id,
            session_id=str(row.id),
            is_active=bool(row.is_active),
        ),
    )


async def _warn_if_linked(
    message: Message,
    *,
    lang: str,
    i18n: I18n,
    sessions: SessionRepository,
    session_id: uuid.UUID,
    excursion_id: str,
) -> bool:
    """True если есть связанные заявки — показано предупреждение, правку блокируем."""
    linked = await sessions.count_applications(session_id)
    confirmed = await sessions.occupied_confirmed(session_id)
    if linked <= 0:
        return False
    await message.answer(
        i18n.t(
            lang,
            "admin.schedule.linked_warning",
            count=linked,
            confirmed=confirmed,
        ),
        reply_markup=admin_schedule_linked_keyboard(
            lang,
            i18n,
            excursion_id=excursion_id,
            session_id=str(session_id),
        ),
    )
    return True


@router.callback_query(AdminScheduleCallback.filter(F.action == "delete"))
async def schedule_delete(
    callback: CallbackQuery,
    callback_data: AdminScheduleCallback,
    state: FSMContext,
    settings: Settings,
    i18n: I18n,
    langs: LanguageStore,
    sessions: SessionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    session_id = uuid.UUID(callback_data.session_id)
    row = await sessions.get_by_id(session_id)
    if row is None:
        await callback.answer(i18n.t(lang, "admin.schedule.not_found"), show_alert=True)
        return
    linked = await sessions.count_applications(session_id)
    await callback.answer()
    if linked > 0:
        await callback.message.answer(
            i18n.t(
                lang,
                "admin.schedule.linked_warning",
                count=linked,
                confirmed=await sessions.occupied_confirmed(session_id),
            ),
            reply_markup=admin_schedule_linked_keyboard(
                lang,
                i18n,
                excursion_id=row.excursion_id,
                session_id=str(row.id),
            ),
        )
        return
    await sessions.deactivate_session(session_id)
    await _send_session_list(
        callback.message,
        state=state,
        lang=lang,
        i18n=i18n,
        sessions=sessions,
        excursion_id=row.excursion_id,
        notice=i18n.t(lang, "admin.schedule.deactivated"),
    )


@router.callback_query(AdminScheduleCallback.filter(F.action == "deactivate_confirm"))
async def schedule_deactivate_confirm(
    callback: CallbackQuery,
    callback_data: AdminScheduleCallback,
    state: FSMContext,
    settings: Settings,
    i18n: I18n,
    langs: LanguageStore,
    sessions: SessionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    row = await sessions.get_by_id(uuid.UUID(callback_data.session_id))
    if row is None:
        await callback.answer(i18n.t(lang, "admin.schedule.not_found"), show_alert=True)
        return
    try:
        await sessions.deactivate_session(row.id)
    except SessionValidationError:
        await callback.answer(i18n.t(lang, "admin.schedule.not_found"), show_alert=True)
        return
    await callback.answer()
    await _send_session_list(
        callback.message,
        state=state,
        lang=lang,
        i18n=i18n,
        sessions=sessions,
        excursion_id=row.excursion_id,
        notice=i18n.t(lang, "admin.schedule.deactivated"),
    )


@router.callback_query(AdminScheduleCallback.filter(F.action == "toggle"))
async def schedule_toggle(
    callback: CallbackQuery,
    callback_data: AdminScheduleCallback,
    state: FSMContext,
    settings: Settings,
    i18n: I18n,
    langs: LanguageStore,
    sessions: SessionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    row = await sessions.get_by_id(uuid.UUID(callback_data.session_id))
    if row is None:
        await callback.answer(i18n.t(lang, "admin.schedule.not_found"), show_alert=True)
        return
    updated = await sessions.update_session(row.id, is_active=not row.is_active)
    await callback.answer(
        i18n.t(
            lang,
            "admin.schedule.toggled",
            active=i18n.t(
                lang,
                "admin.schedule.active_yes"
                if updated.is_active
                else "admin.schedule.active_no",
            ),
        )
    )
    await _send_session_list(
        callback.message,
        state=state,
        lang=lang,
        i18n=i18n,
        sessions=sessions,
        excursion_id=row.excursion_id,
    )


@router.callback_query(AdminScheduleCallback.filter(F.action == "edit_capacity"))
async def schedule_edit_capacity_start(
    callback: CallbackQuery,
    callback_data: AdminScheduleCallback,
    state: FSMContext,
    settings: Settings,
    i18n: I18n,
    langs: LanguageStore,
    sessions: SessionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await callback.answer()
    excursion_id = await _resolve_excursion_id(
        state=state, sessions=sessions, callback_data=callback_data
    )
    await state.update_data(
        schedule_session_id=callback_data.session_id,
        schedule_excursion_id=excursion_id,
    )
    await state.set_state(AdminScheduleForm.edit_capacity)
    await _prompt_with_nav(
        callback.message, lang=lang, i18n=i18n, text_key="admin.schedule.ask_capacity"
    )


@router.message(StateFilter(AdminScheduleForm.edit_capacity))
async def schedule_edit_capacity(
    message: Message,
    state: FSMContext,
    settings: Settings,
    i18n: I18n,
    langs: LanguageStore,
    sessions: SessionRepository,
) -> None:
    lang = await require_admin(message, settings, i18n, langs)
    if not lang or not message.text:
        return
    if await _cancel_input_to_list(
        message, state=state, lang=lang, i18n=i18n, sessions=sessions
    ):
        return
    if not message.text.strip().isdigit() or int(message.text.strip()) <= 0:
        await message.answer(i18n.t(lang, "admin.schedule.error_capacity"))
        await _prompt_with_nav(
            message, lang=lang, i18n=i18n, text_key="admin.schedule.ask_capacity"
        )
        return
    data = await state.get_data()
    session_id = uuid.UUID(str(data["schedule_session_id"]))
    excursion_id = str(data.get("schedule_excursion_id") or "")
    try:
        row = await sessions.update_session(
            session_id, capacity=int(message.text.strip())
        )
        excursion_id = row.excursion_id
    except SessionCapacityError:
        await message.answer(i18n.t(lang, "admin.schedule.error_capacity_below"))
        await _prompt_with_nav(
            message, lang=lang, i18n=i18n, text_key="admin.schedule.ask_capacity"
        )
        return
    except SessionValidationError as error:
        await message.answer(i18n.t(lang, "admin.schedule.error_capacity"))
        await _prompt_with_nav(
            message, lang=lang, i18n=i18n, text_key="admin.schedule.ask_capacity"
        )
        log_event(
            logger,
            action="admin.schedule",
            stage="edit_capacity",
            result="error",
            error_code=error.code,
        )
        return
    except Exception as error:
        from excursion_ai.log_context import get_event_id, log_exception

        error_id = get_event_id()
        log_exception(
            logger,
            action="admin.schedule",
            stage="edit_capacity",
            exc=error,
            result="error",
            error_id=error_id,
            excursion_id=excursion_id,
        )
        await message.answer(
            i18n.t(lang, "admin.schedule.tech_error", error_id=error_id),
            reply_markup=admin_schedule_error_keyboard(lang, i18n),
        )
        return
    await _send_session_list(
        message,
        state=state,
        lang=lang,
        i18n=i18n,
        sessions=sessions,
        excursion_id=excursion_id,
        notice=i18n.t(lang, "admin.schedule.updated", capacity=row.capacity),
    )


@router.callback_query(AdminScheduleCallback.filter(F.action == "edit_time"))
async def schedule_edit_time_start(
    callback: CallbackQuery,
    callback_data: AdminScheduleCallback,
    state: FSMContext,
    settings: Settings,
    i18n: I18n,
    langs: LanguageStore,
    sessions: SessionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await callback.answer()
    excursion_id = await _resolve_excursion_id(
        state=state, sessions=sessions, callback_data=callback_data
    )
    if await _warn_if_linked(
        callback.message,
        lang=lang,
        i18n=i18n,
        sessions=sessions,
        session_id=uuid.UUID(callback_data.session_id),
        excursion_id=excursion_id,
    ):
        return
    await state.update_data(
        schedule_session_id=callback_data.session_id,
        schedule_excursion_id=excursion_id,
    )
    await state.set_state(AdminScheduleForm.edit_time)
    await _prompt_with_nav(
        callback.message, lang=lang, i18n=i18n, text_key="admin.schedule.ask_time"
    )


@router.message(StateFilter(AdminScheduleForm.edit_time))
async def schedule_edit_time(
    message: Message,
    state: FSMContext,
    settings: Settings,
    i18n: I18n,
    langs: LanguageStore,
    sessions: SessionRepository,
) -> None:
    lang = await require_admin(message, settings, i18n, langs)
    if not lang or not message.text:
        return
    if await _cancel_input_to_list(
        message, state=state, lang=lang, i18n=i18n, sessions=sessions
    ):
        return
    try:
        hhmm = parse_rome_time(message.text)
    except SessionValidationError:
        await message.answer(i18n.t(lang, "admin.schedule.error_time"))
        await _prompt_with_nav(
            message, lang=lang, i18n=i18n, text_key="admin.schedule.ask_time"
        )
        return
    data = await state.get_data()
    session_id = uuid.UUID(str(data["schedule_session_id"]))
    current = await sessions.get_by_id(session_id)
    if current is None:
        await message.answer(i18n.t(lang, "admin.schedule.not_found"))
        return
    day = current.starts_at.astimezone(ROME_TZ).date()
    try:
        row = await sessions.update_session(
            session_id, starts_at=combine_rome(day, hhmm)
        )
    except SessionValidationError as error:
        if error.code == "session_has_applications":
            await _warn_if_linked(
                message,
                lang=lang,
                i18n=i18n,
                sessions=sessions,
                session_id=session_id,
                excursion_id=current.excursion_id,
            )
            return
        await message.answer(i18n.t(lang, "admin.schedule.error_duplicate"))
        await _prompt_with_nav(
            message, lang=lang, i18n=i18n, text_key="admin.schedule.ask_time"
        )
        return
    except Exception as error:
        from excursion_ai.log_context import get_event_id, log_exception

        error_id = get_event_id()
        log_exception(
            logger,
            action="admin.schedule",
            stage="edit_time",
            exc=error,
            result="error",
            error_id=error_id,
            excursion_id=current.excursion_id,
        )
        await message.answer(
            i18n.t(lang, "admin.schedule.tech_error", error_id=error_id),
            reply_markup=admin_schedule_error_keyboard(lang, i18n),
        )
        return
    await _send_session_list(
        message,
        state=state,
        lang=lang,
        i18n=i18n,
        sessions=sessions,
        excursion_id=row.excursion_id,
        notice=i18n.t(
            lang, "admin.schedule.time_updated", when=format_rome_datetime(row.starts_at)
        ),
    )


@router.callback_query(AdminScheduleCallback.filter(F.action == "edit_date"))
async def schedule_edit_date_start(
    callback: CallbackQuery,
    callback_data: AdminScheduleCallback,
    state: FSMContext,
    settings: Settings,
    i18n: I18n,
    langs: LanguageStore,
    sessions: SessionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await callback.answer()
    excursion_id = await _resolve_excursion_id(
        state=state, sessions=sessions, callback_data=callback_data
    )
    if await _warn_if_linked(
        callback.message,
        lang=lang,
        i18n=i18n,
        sessions=sessions,
        session_id=uuid.UUID(callback_data.session_id),
        excursion_id=excursion_id,
    ):
        return
    await state.update_data(
        schedule_session_id=callback_data.session_id,
        schedule_excursion_id=excursion_id,
    )
    await state.set_state(AdminScheduleForm.edit_date)
    await _prompt_with_nav(
        callback.message, lang=lang, i18n=i18n, text_key="admin.schedule.ask_date"
    )


@router.message(StateFilter(AdminScheduleForm.edit_date))
async def schedule_edit_date(
    message: Message,
    state: FSMContext,
    settings: Settings,
    i18n: I18n,
    langs: LanguageStore,
    sessions: SessionRepository,
) -> None:
    lang = await require_admin(message, settings, i18n, langs)
    if not lang or not message.text:
        return
    if await _cancel_input_to_list(
        message, state=state, lang=lang, i18n=i18n, sessions=sessions
    ):
        return
    try:
        day = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
    except ValueError:
        await message.answer(i18n.t(lang, "admin.schedule.error_date"))
        await _prompt_with_nav(
            message, lang=lang, i18n=i18n, text_key="admin.schedule.ask_date"
        )
        return
    data = await state.get_data()
    session_id = uuid.UUID(str(data["schedule_session_id"]))
    current = await sessions.get_by_id(session_id)
    if current is None:
        await message.answer(i18n.t(lang, "admin.schedule.not_found"))
        return
    hhmm = current.starts_at.astimezone(ROME_TZ).time().replace(second=0, microsecond=0)
    try:
        row = await sessions.update_session(
            session_id, starts_at=combine_rome(day, hhmm)
        )
    except SessionValidationError as error:
        if error.code == "session_has_applications":
            await _warn_if_linked(
                message,
                lang=lang,
                i18n=i18n,
                sessions=sessions,
                session_id=session_id,
                excursion_id=current.excursion_id,
            )
            return
        await message.answer(i18n.t(lang, "admin.schedule.error_duplicate"))
        await _prompt_with_nav(
            message, lang=lang, i18n=i18n, text_key="admin.schedule.ask_date"
        )
        return
    except Exception as error:
        from excursion_ai.log_context import get_event_id, log_exception

        error_id = get_event_id()
        log_exception(
            logger,
            action="admin.schedule",
            stage="edit_date",
            exc=error,
            result="error",
            error_id=error_id,
            excursion_id=current.excursion_id,
        )
        await message.answer(
            i18n.t(lang, "admin.schedule.tech_error", error_id=error_id),
            reply_markup=admin_schedule_error_keyboard(lang, i18n),
        )
        return
    await _send_session_list(
        message,
        state=state,
        lang=lang,
        i18n=i18n,
        sessions=sessions,
        excursion_id=row.excursion_id,
        notice=i18n.t(
            lang, "admin.schedule.date_updated", when=format_rome_datetime(row.starts_at)
        ),
    )

