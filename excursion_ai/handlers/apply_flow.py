from __future__ import annotations

import logging
import secrets
import uuid
from datetime import date, datetime
from typing import Any

from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from excursion_ai.catalog import Catalog
from excursion_ai.db.applications import ApplicationCreateData, ApplicationRepository
from excursion_ai.db.models import CrmSyncStatus
from excursion_ai.db.sessions import (
    SessionRepository,
    format_rome_date,
    format_rome_time,
)
from excursion_ai.db.users import UserRepository
from excursion_ai.forms.states import ApplyForm
from excursion_ai.forms.texts import (
    form_data_snapshot,
    format_summary,
    money,
)
from excursion_ai.forms.validation import format_date_hint
from excursion_ai.i18n import I18n
from excursion_ai.keyboards import (
    cancel_keyboard,
    confirm_keyboard,
    conflict_keyboard,
    edit_field_keyboard,
    main_menu,
    phone_keyboard,
    schedule_dates_keyboard,
    schedule_times_keyboard,
)
from excursion_ai.log_context import log_event

logger = logging.getLogger(__name__)

FORM_STATES = {
    ApplyForm.participants.state,
    ApplyForm.date.state,
    ApplyForm.time.state,
    ApplyForm.name.state,
    ApplyForm.phone.state,
    ApplyForm.confirm.state,
    ApplyForm.edit_choice.state,
    ApplyForm.conflict.state,
}


async def is_in_form(state: FSMContext) -> bool:
    current = await state.get_state()
    return current in FORM_STATES


async def clear_form(state: FSMContext) -> None:
    await state.clear()


async def start_application_form(
    message: Message,
    *,
    state: FSMContext,
    lang: str,
    i18n: I18n,
    catalog: Catalog,
    excursion_id: str,
) -> None:
    excursion = catalog.get(excursion_id)
    if excursion is None or excursion.status != "published":
        await message.answer(
            i18n.t(lang, "apply.unavailable"),
            reply_markup=main_menu(lang, i18n),
        )
        return

    submission_key = secrets.token_hex(16)
    await state.set_state(ApplyForm.participants)
    await state.set_data(
        {
            "excursion_id": excursion.id,
            "excursion_title": excursion.title(lang),
            "unit_price": str(money(excursion.price_per_person)),
            "currency": excursion.currency,
            "language": lang,
            "submission_key": submission_key,
            "return_to_confirm": False,
            "catalog_version": excursion.version,
        }
    )
    log_event(
        logger,
        action="application.form",
        stage="start",
        result="ok",
        excursion_id=excursion.id,
        lang=lang,
    )
    await message.answer(
        i18n.t(lang, "apply.greeting", title=excursion.title(lang)),
        reply_markup=cancel_keyboard(lang, i18n),
    )
    await message.answer(
        i18n.t(lang, "apply.ask_participants"),
        reply_markup=cancel_keyboard(lang, i18n),
    )


async def prompt_session_dates(
    message: Message,
    *,
    state: FSMContext,
    lang: str,
    i18n: I18n,
    sessions: SessionRepository,
) -> None:
    data = await state.get_data()
    participants = int(data["participants_count"])
    excursion_id = str(data["excursion_id"])
    try:
        dates = await sessions.available_dates(
            excursion_id=excursion_id,
            participants=participants,
        )
    except Exception:
        log_event(
            logger,
            action="application.form",
            stage="dates",
            result="unavailable",
            level=logging.ERROR,
            excursion_id=excursion_id,
        )
        await message.answer(
            i18n.t(lang, "apply.schedule_unavailable"),
            reply_markup=cancel_keyboard(lang, i18n),
        )
        return

    await state.set_state(ApplyForm.date)
    if not dates:
        alts = await sessions.list_available_for_participants(
            excursion_id=excursion_id,
            participants=participants,
            limit=6,
        )
        if not alts:
            await message.answer(
                i18n.t(lang, "apply.no_sessions"),
                reply_markup=cancel_keyboard(lang, i18n),
            )
            return
        lines = [
            i18n.t(lang, "apply.no_sessions_for_date"),
            i18n.t(lang, "apply.nearest_sessions"),
        ]
        day_labels: list[str] = []
        seen: set[str] = set()
        for item in alts:
            label = format_rome_date(item.session.starts_at)
            if label not in seen:
                seen.add(label)
                day_labels.append(label)
            lines.append(
                f"• {format_rome_date(item.session.starts_at)} "
                f"{format_rome_time(item.session.starts_at)} "
                f"({i18n.t(lang, 'apply.seats_left', count=item.free)})"
            )
        await message.answer("\n".join(lines))
        await message.answer(
            i18n.t(lang, "apply.ask_date_pick"),
            reply_markup=schedule_dates_keyboard(lang, i18n, day_labels),
        )
        return

    day_labels = [format_date_hint(day) for day in dates]
    await message.answer(
        i18n.t(lang, "apply.ask_date_pick"),
        reply_markup=schedule_dates_keyboard(lang, i18n, day_labels),
    )


async def prompt_session_times(
    message: Message,
    *,
    state: FSMContext,
    lang: str,
    i18n: I18n,
    sessions: SessionRepository,
    day: date,
) -> None:
    data = await state.get_data()
    participants = int(data["participants_count"])
    excursion_id = str(data["excursion_id"])
    try:
        slots = await sessions.available_times_on_date(
            excursion_id=excursion_id,
            participants=participants,
            day=day,
        )
    except Exception:
        await message.answer(
            i18n.t(lang, "apply.schedule_unavailable"),
            reply_markup=cancel_keyboard(lang, i18n),
        )
        return

    await state.set_state(ApplyForm.time)
    if not slots:
        await message.answer(i18n.t(lang, "apply.no_times_for_date"))
        await prompt_session_dates(
            message, state=state, lang=lang, i18n=i18n, sessions=sessions
        )
        return

    keyboard_slots = [
        (
            str(item.session.id),
            f"{format_rome_time(item.session.starts_at)} · "
            f"{i18n.t(lang, 'apply.seats_left', count=item.free)}",
        )
        for item in slots
    ]
    await message.answer(
        i18n.t(lang, "apply.ask_time_pick", date=format_date_hint(day)),
        reply_markup=schedule_times_keyboard(lang, i18n, keyboard_slots),
    )


async def ask_conflict(
    message: Message,
    *,
    lang: str,
    i18n: I18n,
    state: FSMContext,
    pending_action: str,
    pending_excursion_id: str = "",
) -> None:
    data = await state.get_data()
    data["pending_action"] = pending_action
    data["pending_excursion_id"] = pending_excursion_id
    previous_state = await state.get_state()
    data["resume_state"] = previous_state
    await state.set_data(data)
    await state.set_state(ApplyForm.conflict)
    log_event(
        logger,
        action="application.form",
        stage="conflict",
        result="prompt",
        pending_action=pending_action,
    )
    await message.answer(
        i18n.t(lang, "apply.conflict"),
        reply_markup=conflict_keyboard(lang, i18n),
    )


async def resume_current_step(
    message: Message,
    *,
    state: FSMContext,
    lang: str,
    i18n: I18n,
    catalog: Catalog,
    sessions: SessionRepository | None = None,
) -> None:
    current = await state.get_state()
    data = await state.get_data()
    data["language"] = lang
    excursion = catalog.get(str(data.get("excursion_id", "")))
    if excursion is not None:
        data["excursion_title"] = excursion.title(lang)
        data["unit_price"] = str(money(excursion.price_per_person))
        data["currency"] = excursion.currency
    await state.set_data(data)

    if current == ApplyForm.participants.state:
        await message.answer(
            i18n.t(lang, "apply.ask_participants"),
            reply_markup=cancel_keyboard(lang, i18n),
        )
    elif current == ApplyForm.date.state and sessions is not None:
        await prompt_session_dates(
            message, state=state, lang=lang, i18n=i18n, sessions=sessions
        )
    elif current == ApplyForm.time.state and sessions is not None and data.get("requested_date"):
        await prompt_session_times(
            message,
            state=state,
            lang=lang,
            i18n=i18n,
            sessions=sessions,
            day=date.fromisoformat(str(data["requested_date"])),
        )
    elif current == ApplyForm.name.state:
        await message.answer(
            i18n.t(lang, "apply.ask_name"),
            reply_markup=cancel_keyboard(lang, i18n),
        )
    elif current == ApplyForm.phone.state:
        await message.answer(
            i18n.t(lang, "apply.ask_phone"),
            reply_markup=phone_keyboard(lang, i18n),
        )
    elif current == ApplyForm.confirm.state:
        await send_summary(message, state=state, lang=lang, i18n=i18n)
    elif current == ApplyForm.edit_choice.state:
        await message.answer(
            i18n.t(lang, "apply.edit_choose"),
            reply_markup=edit_field_keyboard(lang, i18n),
        )
    else:
        await message.answer(
            i18n.t(lang, "apply.ask_participants"),
            reply_markup=cancel_keyboard(lang, i18n),
        )


async def send_summary(
    message: Message,
    *,
    state: FSMContext,
    lang: str,
    i18n: I18n,
) -> None:
    data = await state.get_data()
    requested = date.fromisoformat(str(data["requested_date"]))
    starts_raw = data.get("session_starts_at")
    starts = datetime.fromisoformat(str(starts_raw)) if starts_raw else None
    text = format_summary(
        lang=lang,
        i18n=i18n,
        excursion_title=str(data["excursion_title"]),
        requested_date=requested,
        session_time=format_rome_time(starts) if starts else "—",
        participants_count=int(data["participants_count"]),
        customer_name=str(data["customer_name"]),
        phone=data.get("phone"),
        unit_price=money(str(data["unit_price"])),
        currency=str(data["currency"]),
    )
    note = i18n.t(lang, "apply.availability_note").strip()
    if note:
        text = f"{text}\n\n{note}"
    await state.set_state(ApplyForm.confirm)
    await message.answer(
        text,
        reply_markup=confirm_keyboard(lang, i18n),
        parse_mode="HTML",
    )


async def cancel_form(
    message: Message,
    *,
    state: FSMContext,
    lang: str,
    i18n: I18n,
) -> None:
    data = await state.get_data()
    log_event(
        logger,
        action="application.form",
        stage="cancel",
        result="ok",
        **form_data_snapshot(data),
    )
    await clear_form(state)
    await message.answer(
        i18n.t(lang, "apply.cancelled"),
        reply_markup=main_menu(lang, i18n),
    )


def build_create_data(data: dict[str, Any]) -> ApplicationCreateData:
    session_id_raw = data.get("session_id")
    starts_raw = data.get("session_starts_at")
    if not session_id_raw or not starts_raw:
        raise ValueError("session_required")
    return ApplicationCreateData(
        submission_key=str(data["submission_key"]),
        excursion_id=str(data["excursion_id"]),
        excursion_title=str(data["excursion_title"]),
        language=str(data["language"]),
        requested_date=date.fromisoformat(str(data["requested_date"])),
        session_id=uuid.UUID(str(session_id_raw)),
        session_starts_at=datetime.fromisoformat(str(starts_raw)),
        participants_count=int(data["participants_count"]),
        customer_name=str(data["customer_name"]),
        phone=data.get("phone"),
        is_demo=True,
        unit_price=money(str(data["unit_price"])),
        currency=str(data["currency"]),
    )


async def refresh_offer_from_catalog(
    state: FSMContext,
    *,
    catalog: Catalog,
    lang: str,
) -> str | None:
    data = await state.get_data()
    excursion_id = str(data.get("excursion_id") or "")
    excursion = catalog.get(excursion_id)
    if excursion is None or excursion.status != "published":
        return "unavailable"
    new_price = money(excursion.price_per_person)
    new_title = excursion.title(lang)
    new_currency = excursion.currency
    old_price = money(str(data.get("unit_price")))
    changed = (
        new_price != old_price
        or new_currency != str(data.get("currency"))
        or new_title != str(data.get("excursion_title"))
    )
    data["excursion_title"] = new_title
    data["unit_price"] = str(new_price)
    data["currency"] = new_currency
    data["catalog_version"] = excursion.version
    data["language"] = lang
    await state.set_data(data)
    return "price_changed" if changed else None


async def save_application(
    *,
    telegram_user_id: int,
    state: FSMContext,
    users: UserRepository,
    applications: ApplicationRepository,
    lang: str,
    crm_ready: bool = False,
) -> Any:
    data = await state.get_data()
    if data.get("saved_application_id"):
        existing = await applications.get_for_user(
            application_id=uuid.UUID(str(data["saved_application_id"])),
            telegram_user_id=telegram_user_id,
        )
        if existing is not None:
            log_event(
                logger,
                action="application.save",
                stage="already_saved",
                result="ok",
                excursion_id=data.get("excursion_id"),
            )
            return existing

    await users.get_or_create(telegram_user_id, language=lang)
    payload = build_create_data(data)
    data["excursion_title"] = payload.excursion_title
    data["unit_price"] = str(payload.unit_price)
    data["currency"] = payload.currency
    await state.set_data(data)

    crm_status = (
        CrmSyncStatus.PENDING.value if crm_ready else CrmSyncStatus.NOT_CONNECTED.value
    )
    application = await applications.create_application(
        telegram_user_id=telegram_user_id,
        data=payload,
        crm_sync_status=crm_status,
    )
    data["saved_application_id"] = str(application.id)
    await state.set_data(data)
    log_event(
        logger,
        action="application.save",
        stage="commit",
        result="ok",
        excursion_id=payload.excursion_id,
        application_id=str(application.id),
        session_id=str(payload.session_id),
        crm_sync_status=crm_status,
    )
    return application


def date_error_text(lang: str, i18n: I18n, code: str) -> str:
    return i18n.t(lang, f"apply.error.{code}")
