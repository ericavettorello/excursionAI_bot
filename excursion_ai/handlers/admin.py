from __future__ import annotations

import asyncio
import html
import logging
import re
import secrets
from decimal import Decimal, InvalidOperation
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from excursion_ai.ai.errors import (
    AIBusyError,
    AIConfigError,
    AIError,
    AILimitError,
    AIValidationError,
)
from excursion_ai.ai.translation import AdminTranslationService
from excursion_ai.catalog import Catalog, Excursion
from excursion_ai.catalog_store import CatalogStore
from excursion_ai.config import SUPPORTED_LANGS, Settings
from excursion_ai.db.excursions import (
    MAX_PHOTOS,
    REVIEW_CONFIRMED,
    REVIEW_PENDING,
    REVIEW_STALE,
    ExcursionRepository,
    ExcursionRepositoryError,
    ExcursionVersionConflict,
)
from excursion_ai.db.models import ExcursionStatus
from excursion_ai.db.sessions import SessionRepository, format_rome_datetime
from excursion_ai.db.translation_meta import fingerprint_translation_block, other_languages
from excursion_ai.forms.admin_states import AdminExcursionForm
from excursion_ai.handlers.admin_access import require_admin
from excursion_ai.handlers.apply_flow import ask_conflict, is_in_form
from excursion_ai.handlers.catalog import send_catalog_list
from excursion_ai.handlers.common import MenuText
from excursion_ai.handlers.consult_state import leave_consult_mode
from excursion_ai.handlers.start import require_language, send_welcome
from excursion_ai.i18n import I18n
from excursion_ai.keyboards import (
    ADMIN_PAGE_SIZE,
    AdminCallback,
    admin_card_keyboard,
    admin_confirm_pending_keyboard,
    admin_edit_confirm_keyboard,
    admin_edit_data_keyboard,
    admin_edit_menu_keyboard,
    admin_edit_route_keyboard,
    admin_edit_translation_keyboard,
    admin_lang_conducted,
    admin_lang_hub_keyboard,
    admin_list_keyboard,
    admin_main_keyboard,
    admin_photo_manage_keyboard,
    admin_photo_stage_keyboard,
    admin_preview_keyboard,
    admin_publish_confirm_keyboard,
    admin_publish_missing_keyboard,
    admin_published_done_keyboard,
    admin_archive_confirm_keyboard,
    admin_retranslate_confirm_keyboard,
    admin_source_lang_keyboard,
    admin_summary_keyboard,
    admin_translate_offer_keyboard,
    admin_translate_review_keyboard,
    admin_version_conflict_keyboard,
    admin_wizard_nav,
)
from excursion_ai.languages import LanguageStore
from excursion_ai.log_context import log_event

router = Router(name="admin")
logger = logging.getLogger(__name__)

CITY_RE = re.compile(r"^[a-z]{2,32}$")
MESSAGE_LIMIT = 3500
MEDIA_GROUP_DEBOUNCE_SEC = 1.2

# media_group_id -> buffered photo payloads awaiting flush
_media_group_buffers: dict[str, list[dict[str, str]]] = {}
_media_group_tasks: dict[str, asyncio.Task[None]] = {}
_media_group_meta: dict[str, dict[str, Any]] = {}


async def _answer_chunks(
    message: Message,
    text: str,
    *,
    reply_markup: Any = None,
    parse_mode: str | None = "HTML",
) -> None:
    if len(text) <= MESSAGE_LIMIT:
        await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
        return
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= MESSAGE_LIMIT:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, MESSAGE_LIMIT)
        if cut < MESSAGE_LIMIT // 2:
            cut = MESSAGE_LIMIT
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    for index, chunk in enumerate(chunks):
        markup = reply_markup if index == len(chunks) - 1 else None
        await message.answer(chunk, reply_markup=markup, parse_mode=parse_mode)


def _list_label(excursion: Excursion, lang: str) -> str:
    block = excursion.translations.get(lang) or excursion.translations.get("ru")
    title = str(block["title"]) if block and block.get("title") else excursion.id
    return f"{title} · {excursion.id}"


def _display_title(excursion: Excursion, preferred: str, i18n: I18n, ui_lang: str) -> str:
    for code in (preferred, "ru", "en", "it"):
        block = excursion.translations.get(code)
        if block and str(block.get("title") or "").strip():
            return str(block["title"]).strip()
    return i18n.t(ui_lang, "admin.untitled") + f" ({excursion.city_id})"


def _translation_ready(excursion: Excursion, code: str) -> bool:
    block = excursion.translations.get(code)
    if not block:
        return False
    for field in ("title", "description", "meeting_point"):
        if not str(block.get(field, "")).strip():
            return False
    route = block.get("route")
    return isinstance(route, list) and 3 <= len(route) <= 4


def _ready_map(excursion: Excursion) -> dict[str, bool]:
    return {code: _translation_ready(excursion, code) for code in SUPPORTED_LANGS}


def _review_status_code(excursion: Excursion, code: str) -> str:
    if not _translation_ready(excursion, code):
        return "missing"
    status = excursion.meta_for(code).review_status
    if status in {REVIEW_CONFIRMED, REVIEW_PENDING, REVIEW_STALE}:
        return status
    return REVIEW_PENDING


def _resolve_source_language(excursion: Excursion) -> str | None:
    if excursion.source_language in SUPPORTED_LANGS:
        return excursion.source_language
    if _translation_ready(excursion, "ru"):
        return "ru"
    for code in SUPPORTED_LANGS:
        if _translation_ready(excursion, code):
            return code
    return None


def _photo_count(excursion: Excursion) -> int:
    if excursion.photos:
        return len(excursion.photos)
    return 1 if excursion.photo_file_id else 0


def _missing_required(excursion: Excursion, *, lang: str, i18n: I18n) -> list[str]:
    missing: list[str] = []
    if excursion.duration_minutes <= 0:
        missing.append(i18n.t(lang, "admin.missing.duration"))
    if excursion.price_per_person < 0:
        missing.append(i18n.t(lang, "admin.missing.price"))
    if not excursion.conducted_languages:
        missing.append(i18n.t(lang, "admin.missing.languages"))
    field_keys = (
        ("title", "admin.field.title"),
        ("description", "admin.field.description"),
        ("route", "admin.field.route"),
        ("meeting_point", "admin.field.meeting"),
    )
    for code in SUPPORTED_LANGS:
        block = excursion.translations.get(code) or {}
        incomplete = False
        for field, field_key in field_keys:
            if field == "route":
                route = block.get("route")
                ok = isinstance(route, list) and 3 <= len(route) <= 4
            else:
                ok = bool(str(block.get(field, "")).strip())
            if not ok:
                incomplete = True
                missing.append(
                    i18n.t(
                        lang,
                        "admin.missing.translation",
                        code=code.upper(),
                        field=i18n.t(lang, field_key),
                    )
                )
        if not incomplete and _review_status_code(excursion, code) != REVIEW_CONFIRMED:
            missing.append(i18n.t(lang, "admin.missing.review", code=code.upper()))
    return missing


def _format_admin_schedule_block(
    *,
    lang: str,
    i18n: I18n,
    session_rows: list[Any],
) -> list[str]:
    lines = ["", html.escape(i18n.t(lang, "admin.summary_schedule_header"))]
    if not session_rows:
        lines.append(html.escape(i18n.t(lang, "admin.summary_schedule_empty")))
        return lines
    for row in session_rows:
        lines.append(
            html.escape(
                i18n.t(
                    lang,
                    "admin.summary_schedule_item",
                    when=format_rome_datetime(row.starts_at),
                    capacity=row.capacity,
                )
            )
        )
    return lines


def _build_summary_text(
    excursion: Excursion,
    *,
    lang: str,
    i18n: I18n,
    session_rows: list[Any] | None = None,
    schedule_missing: bool = False,
) -> str:
    title = _display_title(excursion, lang, i18n, lang)
    ready = _ready_map(excursion)
    source = _resolve_source_language(excursion) or "-"
    lines = [
        f"<b>{html.escape(i18n.t(lang, 'admin.summary_title', title=title))}</b>",
        html.escape(i18n.t(lang, "admin.summary_status", status=excursion.status)),
        html.escape(
            i18n.t(
                lang,
                "admin.summary_meta",
                city_id=excursion.city_id,
                duration=excursion.duration_minutes,
                price=excursion.price_per_person,
                currency=excursion.currency,
                langs=",".join(excursion.conducted_languages) or "-",
            )
        ),
        html.escape(
            i18n.t(lang, "admin.summary_source", code=str(source).upper())
        ),
        "",
    ]
    for code in SUPPORTED_LANGS:
        fill_key = (
            f"admin.lang_ready.{code}" if ready[code] else f"admin.lang_missing.{code}"
        )
        fill = i18n.t(lang, fill_key)
        review = i18n.t(lang, f"admin.review.{_review_status_code(excursion, code)}")
        lines.append(
            html.escape(
                i18n.t(
                    lang,
                    "admin.summary_lang_line",
                    code=code.upper(),
                    fill=fill.split(": ", 1)[-1] if ": " in fill else fill,
                    review=review,
                )
            )
        )
    lines.extend(
        [
            "",
            html.escape(
                i18n.t(
                    lang,
                    "admin.summary_photo_count",
                    count=_photo_count(excursion),
                )
            ),
        ]
    )
    lines.extend(
        _format_admin_schedule_block(
            lang=lang, i18n=i18n, session_rows=session_rows or []
        )
    )
    lines.append("")
    missing = _missing_required(excursion, lang=lang, i18n=i18n)
    if schedule_missing:
        missing.append(i18n.t(lang, "admin.missing.schedule"))
    if missing:
        lines.append(html.escape(i18n.t(lang, "admin.summary_missing_header")))
        for item in missing:
            lines.append(
                html.escape(i18n.t(lang, "admin.summary_missing_item", item=item))
            )
    else:
        lines.append(html.escape(i18n.t(lang, "admin.summary_missing_none")))
    return "\n".join(lines)


def _escape_preview(
    excursion: Excursion,
    lang: str,
    i18n: I18n,
    session_rows: list[Any] | None = None,
) -> str:
    title = _display_title(excursion, lang, i18n, lang)
    lines = [
        f"<b>{html.escape(i18n.t(lang, 'admin.card_title', title=title, id=excursion.id))}</b>",
        html.escape(
            i18n.t(
                lang,
                "admin.card_meta",
                status=excursion.status,
                version=excursion.version,
                city_id=excursion.city_id,
            )
        ),
        "",
        f"duration: {excursion.duration_minutes} min",
        f"price: {excursion.price_per_person} {html.escape(excursion.currency)}",
        f"langs: {html.escape(','.join(excursion.conducted_languages) or '-')}",
    ]
    lines.extend(
        _format_admin_schedule_block(
            lang=lang, i18n=i18n, session_rows=session_rows or []
        )
    )
    for code in SUPPORTED_LANGS:
        block = excursion.translations.get(code)
        lines.append("")
        lines.append(f"<b>{html.escape(code.upper())}</b>")
        if not block:
            lines.append("—")
            continue
        title = html.escape(str(block.get("title", "")))
        city = html.escape(str(block.get("city", "")))
        description = html.escape(str(block.get("description", "")))
        meeting = html.escape(str(block.get("meeting_point", "")))
        route_raw = block.get("route") or []
        route_items = route_raw if isinstance(route_raw, list) else []
        route_lines = "\n".join(
            f"{index}. {html.escape(str(item))}"
            for index, item in enumerate(route_items, start=1)
        )
        lines.extend([title, city, description, meeting, route_lines or "—"])
    return "\n".join(lines)


async def _has_pending(excursions: ExcursionRepository, excursion_id: str) -> bool:
    row = await excursions.get_row(excursion_id)
    return bool(row and row.pending_patch)


async def _bind_wizard_excursion(
    state: FSMContext,
    excursion: Excursion,
    *,
    wizard: bool = True,
) -> None:
    source = _resolve_source_language(excursion)
    data = {
        "wizard": wizard,
        "excursion_id": excursion.id,
        "city_id": excursion.city_id,
        "conducted": list(excursion.conducted_languages),
        "lang_index": SUPPORTED_LANGS.index(source) if source in SUPPORTED_LANGS else 0,
        "draft_buffer": {},
    }
    if source:
        data["source_lang"] = source
        data["active_lang"] = source
    await state.set_data(data)


async def _send_home(
    message: Message,
    *,
    lang: str,
    i18n: I18n,
    state: FSMContext,
) -> None:
    await state.clear()
    await message.answer(
        f"{i18n.t(lang, 'admin.home')}\n\n{i18n.t(lang, 'admin.home_hint')}",
        reply_markup=admin_main_keyboard(lang, i18n),
    )


async def _send_list(
    message: Message,
    *,
    lang: str,
    i18n: I18n,
    excursions: ExcursionRepository,
    status: str,
    page: int = 0,
) -> None:
    items = await excursions.list_by_status(status)
    pages = max(1, (len(items) + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    slice_items = items[page * ADMIN_PAGE_SIZE : (page + 1) * ADMIN_PAGE_SIZE]
    status_label = i18n.t(lang, f"admin.status.{status}")
    header = i18n.t(lang, "admin.list_title", status=status_label)
    if not items:
        body = f"{header}\n\n{i18n.t(lang, 'admin.list_empty')}"
    else:
        body = f"{header}\n{i18n.t(lang, 'admin.list_page', page=page + 1, pages=pages)}"
    await message.answer(
        body,
        reply_markup=admin_list_keyboard(
            lang,
            i18n,
            status=status,
            items=[(item.id, _list_label(item, lang)) for item in slice_items],
            page=page,
            pages=pages,
        ),
    )
    log_event(
        logger,
        action="admin.ui",
        stage="list",
        result="ok",
        status=status,
        count=len(items),
        page=page,
    )


def _session_repo(
    excursions: ExcursionRepository,
    sessions: SessionRepository | None = None,
) -> SessionRepository:
    if sessions is not None:
        return sessions
    return SessionRepository(excursions._session_factory)


async def _send_card(
    message: Message,
    *,
    lang: str,
    i18n: I18n,
    excursions: ExcursionRepository,
    excursion_id: str,
    list_status: str = "",
    page: int = 0,
    notice: str = "",
    sessions: SessionRepository | None = None,
) -> None:
    excursion = await excursions.get(excursion_id)
    if excursion is None:
        await message.answer(i18n.t(lang, "admin.not_found"))
        return
    has_pending = await _has_pending(excursions, excursion_id)
    session_repo = _session_repo(excursions, sessions)
    session_rows = await session_repo.list_for_excursion(
        excursion_id, include_inactive=True
    )
    active_count = sum(1 for row in session_rows if row.is_active)
    publishable = excursions.is_publishable(excursion) and active_count > 0
    if excursion.status == ExcursionStatus.DRAFT.value:
        parts = [
            _build_summary_text(
                excursion,
                lang=lang,
                i18n=i18n,
                session_rows=session_rows,
                schedule_missing=active_count < 1,
            )
        ]
    else:
        ready_key = "admin.card_ready" if publishable else "admin.card_not_ready"
        parts = [
            _escape_preview(excursion, lang, i18n, session_rows=session_rows),
            "",
            i18n.t(lang, ready_key),
        ]
    if has_pending:
        parts.append(i18n.t(lang, "admin.card_pending"))
    if notice:
        parts = [html.escape(notice), ""] + parts
    text = "\n".join(parts)
    markup = admin_card_keyboard(
        lang,
        i18n,
        excursion_id=excursion.id,
        status=excursion.status,
        has_pending=has_pending,
        publishable=publishable,
        list_status=list_status or excursion.status,
        page=page,
    )
    if excursion.photo_file_id:
        caption = text if len(text) <= 1024 else text[:1000] + "…"
        await message.answer_photo(
            excursion.photo_file_id,
            caption=caption,
            reply_markup=markup if len(text) <= 1024 else None,
            parse_mode="HTML",
        )
        if len(text) > 1024:
            await _answer_chunks(message, text, reply_markup=markup)
    else:
        await _answer_chunks(message, text, reply_markup=markup)
    log_event(
        logger,
        action="admin.ui",
        stage="card",
        result="ok",
        excursion_id=excursion.id,
        status=excursion.status,
    )


async def _send_summary(
    message: Message,
    *,
    lang: str,
    i18n: I18n,
    excursions: ExcursionRepository,
    excursion_id: str,
    notice: str = "",
    show_translate_en_it: bool = False,
    sessions: SessionRepository | None = None,
) -> None:
    excursion = await excursions.get(excursion_id)
    if excursion is None:
        await message.answer(i18n.t(lang, "admin.not_found"))
        return
    session_repo = _session_repo(excursions, sessions)
    session_rows = await session_repo.list_for_excursion(
        excursion_id, include_inactive=True
    )
    active_count = sum(1 for row in session_rows if row.is_active)
    text = _build_summary_text(
        excursion,
        lang=lang,
        i18n=i18n,
        session_rows=session_rows,
        schedule_missing=active_count < 1,
    )
    if notice:
        text = f"{html.escape(notice)}\n\n{text}"
    await _answer_chunks(
        message,
        text,
        reply_markup=admin_summary_keyboard(
            lang,
            i18n,
            excursion_id=excursion.id,
            show_translate_en_it=show_translate_en_it,
        ),
    )


async def _send_summary_after_source(
    message: Message,
    *,
    state: FSMContext,
    lang: str,
    i18n: I18n,
    excursions: ExcursionRepository,
    excursion_id: str,
    notice: str = "",
) -> None:
    excursion = await excursions.get(excursion_id)
    if excursion is None:
        await message.answer(i18n.t(lang, "admin.not_found"))
        return
    source = _resolve_source_language(excursion)
    show_translate = bool(source) and _translation_ready(excursion, source)
    if show_translate:
        for target in other_languages(source or ""):
            if not _translation_ready(excursion, target):
                break
        else:
            show_translate = False
    await _bind_wizard_excursion(state, excursion, wizard=True)
    await state.set_state(AdminExcursionForm.preview)
    await _send_summary(
        message,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        excursion_id=excursion_id,
        notice=notice,
        show_translate_en_it=show_translate,
    )


async def _send_lang_hub(
    message: Message,
    *,
    state: FSMContext,
    lang: str,
    i18n: I18n,
    excursions: ExcursionRepository,
    excursion_id: str,
    notice: str = "",
) -> None:
    excursion = await excursions.get(excursion_id)
    if excursion is None:
        await message.answer(i18n.t(lang, "admin.not_found"))
        return
    await _bind_wizard_excursion(state, excursion, wizard=True)
    await state.set_state(AdminExcursionForm.lang_hub)
    title = _display_title(excursion, lang, i18n, lang)
    parts: list[str] = []
    if notice:
        parts.append(notice)
    parts.append(i18n.t(lang, "admin.hub_title", title=title))
    ready = _ready_map(excursion)
    for code in SUPPORTED_LANGS:
        key = f"admin.lang_ready.{code}" if ready[code] else f"admin.lang_missing.{code}"
        parts.append(i18n.t(lang, key))
    await message.answer(
        "\n".join(parts),
        reply_markup=admin_lang_hub_keyboard(
            lang, i18n, excursion_id=excursion.id, ready=ready
        ),
    )


async def _send_preview(
    message: Message,
    *,
    lang: str,
    i18n: I18n,
    excursions: ExcursionRepository,
    excursion_id: str,
) -> None:
    excursion = await excursions.get(excursion_id)
    if excursion is None:
        await message.answer(i18n.t(lang, "admin.not_found"))
        return
    session_repo = _session_repo(excursions)
    session_rows = await session_repo.list_for_excursion(
        excursion_id, include_inactive=True
    )
    active_count = sum(1 for row in session_rows if row.is_active)
    publishable = excursions.is_publishable(excursion) and active_count > 0
    await message.answer(i18n.t(lang, "admin.preview_done"))
    await _answer_chunks(
        message,
        _escape_preview(excursion, lang, i18n, session_rows=session_rows),
        reply_markup=admin_preview_keyboard(
            lang,
            i18n,
            excursion_id=excursion.id,
            publishable=publishable,
        ),
    )


async def _version_conflict(
    message: Message,
    *,
    lang: str,
    i18n: I18n,
    excursion_id: str,
) -> None:
    await message.answer(
        i18n.t(lang, "admin.version_conflict"),
        reply_markup=admin_version_conflict_keyboard(
            lang, i18n, excursion_id=excursion_id
        ),
    )
    log_event(
        logger,
        action="admin.excursion",
        stage="version_conflict",
        result="conflict",
        excursion_id=excursion_id,
    )


async def _handle_repo_error(
    message: Message,
    *,
    lang: str,
    i18n: I18n,
    error: ExcursionRepositoryError,
    excursion_id: str = "",
) -> None:
    if isinstance(error, ExcursionVersionConflict):
        await _version_conflict(message, lang=lang, i18n=i18n, excursion_id=excursion_id)
        return
    key_map = {
        "not_found": "admin.not_found",
        "not_ready": "admin.not_ready",
        "pending_exists": "admin.pending_exists",
        "no_pending": "admin.not_found",
        "no_sessions": "admin.missing.schedule",
    }
    await message.answer(i18n.t(lang, key_map.get(error.code, "admin.error.generic")))
    log_event(
        logger,
        action="admin.excursion",
        stage="error",
        result=error.code,
        excursion_id=excursion_id or "-",
    )


def _parse_city(text: str) -> str | None:
    value = text.strip().lower()
    return value if CITY_RE.fullmatch(value) else None


def _parse_duration(text: str) -> int | None:
    try:
        value = int(text.strip())
    except ValueError:
        return None
    return value if 15 <= value <= 720 else None


def _parse_price(text: str) -> Decimal | None:
    raw = text.strip().replace(",", ".")
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    if value < 0:
        return None
    return value.quantize(Decimal("0.01"))


def _parse_title(text: str) -> str | None:
    value = text.strip()
    return value if 2 <= len(value) <= 200 else None


def _parse_description(text: str) -> str | None:
    value = text.strip()
    return value if 10 <= len(value) <= 2000 else None


def _parse_route(text: str) -> list[str] | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines if 3 <= len(lines) <= 4 else None


def _parse_meeting(text: str) -> str | None:
    value = text.strip()
    return value if 5 <= len(value) <= 500 else None


TEXT_WIZARD_STEPS = ("title", "description", "route", "meeting")

_ASK_TEXT_FIELD_KEYS = {
    "title": "admin.ask_title",
    "description": "admin.ask_description",
    "route": "admin.ask_route",
    "meeting": "admin.ask_meeting",
}


def _merged_lang_text_block(
    excursion: Excursion,
    code: str,
    buffer: dict[str, Any] | None,
) -> dict[str, Any]:
    base = dict(excursion.translations.get(code) or {})
    draft = dict((buffer or {}).get(code) or {})
    block: dict[str, Any] = {
        "title": draft["title"] if "title" in draft else base.get("title", ""),
        "description": draft["description"]
        if "description" in draft
        else base.get("description", ""),
        "route": draft["route"] if "route" in draft else base.get("route", []),
        "meeting_point": draft["meeting_point"]
        if "meeting_point" in draft
        else base.get("meeting_point", ""),
    }
    return block


def _text_step_satisfied(block: dict[str, Any], step: str) -> bool:
    if step == "title":
        return _parse_title(str(block.get("title", ""))) is not None
    if step == "description":
        return _parse_description(str(block.get("description", ""))) is not None
    if step == "route":
        route = block.get("route")
        if isinstance(route, list):
            return _parse_route("\n".join(str(x) for x in route)) is not None
        if isinstance(route, str):
            return _parse_route(route) is not None
        return False
    if step == "meeting":
        return _parse_meeting(str(block.get("meeting_point", ""))) is not None
    return False


def _next_text_step(step: str) -> str | None:
    try:
        index = TEXT_WIZARD_STEPS.index(step)
    except ValueError:
        return None
    if index + 1 >= len(TEXT_WIZARD_STEPS):
        return None
    return TEXT_WIZARD_STEPS[index + 1]


def _first_open_text_step(
    excursion: Excursion,
    code: str,
    buffer: dict[str, Any] | None,
) -> str | None:
    block = _merged_lang_text_block(excursion, code, buffer)
    for step in TEXT_WIZARD_STEPS:
        if not _text_step_satisfied(block, step):
            return step
    return None


def _title_hint_for_prompt(
    excursion: Excursion,
    code: str,
    buffer: dict[str, Any] | None,
    *,
    ui_lang: str,
    i18n: I18n,
) -> str:
    block = _merged_lang_text_block(excursion, code, buffer)
    title = str(block.get("title") or "").strip()
    if title:
        return title
    return _display_title(excursion, code, i18n, ui_lang)


def _format_admin_text_field_prompt(
    lang: str,
    i18n: I18n,
    step: str,
    *,
    title_hint: str,
    target_code: str,
) -> str:
    key = _ASK_TEXT_FIELD_KEYS[step]
    lang_block = i18n.t(lang, f"admin.lang_block.{target_code}")
    return i18n.t(
        lang,
        key,
        title=title_hint,
        lang_block=lang_block,
    )


def _active_lang_code(data: dict[str, Any]) -> str:
    explicit = str(data.get("active_lang") or "")
    if explicit in SUPPORTED_LANGS:
        return explicit
    index = int(data.get("lang_index", 0))
    if 0 <= index < len(SUPPORTED_LANGS):
        return SUPPORTED_LANGS[index]
    return SUPPORTED_LANGS[0]


async def _prompt_translation_step(
    message: Message,
    *,
    state: FSMContext,
    lang: str,
    i18n: I18n,
    excursions: ExcursionRepository,
    step: str,
) -> None:
    data = await state.get_data()
    code = _active_lang_code(data)
    excursion_id = str(data.get("excursion_id") or "")
    excursion = await excursions.get(excursion_id) if excursion_id else None
    if excursion is None:
        await message.answer(i18n.t(lang, "admin.not_found"))
        return
    buffer = dict(data.get("draft_buffer") or {})
    title_hint = _title_hint_for_prompt(
        excursion, code, buffer, ui_lang=lang, i18n=i18n
    )
    await state.set_state(
        {
            "title": AdminExcursionForm.title,
            "description": AdminExcursionForm.description,
            "route": AdminExcursionForm.route,
            "meeting": AdminExcursionForm.meeting,
        }[step]
    )
    await message.answer(
        _format_admin_text_field_prompt(
            lang,
            i18n,
            step,
            title_hint=title_hint,
            target_code=code,
        ),
        reply_markup=admin_wizard_nav(lang, i18n),
    )


async def _advance_text_wizard(
    message: Message,
    *,
    state: FSMContext,
    lang: str,
    i18n: I18n,
    excursions: ExcursionRepository,
    catalog_store: CatalogStore,
    completed_step: str,
) -> None:
    data = await state.get_data()
    code = _active_lang_code(data)
    excursion_id = str(data.get("excursion_id") or "")
    excursion = await excursions.get(excursion_id)
    if excursion is None:
        await message.answer(i18n.t(lang, "admin.not_found"))
        return
    buffer = dict(data.get("draft_buffer") or {})
    next_step = _next_text_step(completed_step)
    while next_step and _text_step_satisfied(
        _merged_lang_text_block(excursion, code, buffer), next_step
    ):
        next_step = _next_text_step(next_step)
    if next_step:
        await _prompt_translation_step(
            message,
            state=state,
            lang=lang,
            i18n=i18n,
            excursions=excursions,
            step=next_step,
        )
        return
    await _commit_language_text_block(
        message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        catalog_store=catalog_store,
        code=code,
    )


async def _commit_language_text_block(
    message: Message,
    *,
    state: FSMContext,
    lang: str,
    i18n: I18n,
    excursions: ExcursionRepository,
    catalog_store: CatalogStore,
    code: str,
) -> None:
    data = await state.get_data()
    excursion_id = str(data["excursion_id"])
    buffer = dict(data.get("draft_buffer") or {})
    block = dict(buffer.get(code) or {})
    excursion = await excursions.get(excursion_id)
    if excursion is None:
        await message.answer(i18n.t(lang, "admin.not_found"))
        return
    merged = _merged_lang_text_block(excursion, code, buffer)
    if _first_open_text_step(excursion, code, buffer) is not None:
        step = _first_open_text_step(excursion, code, buffer)
        assert step is not None
        await _prompt_translation_step(
            message,
            state=state,
            lang=lang,
            i18n=i18n,
            excursions=excursions,
            step=step,
        )
        return
    source = str(data.get("source_lang") or excursion.source_language or "")
    is_source = bool(source) and code == source
    meeting = str(merged["meeting_point"])
    try:
        await excursions.upsert_translation(
            excursion_id=excursion_id,
            language=code,
            title=str(merged["title"]),
            city_label=str(data.get("city_id") or excursion.city_id),
            description=str(merged["description"]),
            route=list(merged["route"]),
            meeting_point=meeting,
            expected_version=excursion.version,
            apply_immediately=True,
            review_status=REVIEW_CONFIRMED,
            origin="manual",
            mark_others_stale_from_source=is_source,
        )
    except ExcursionRepositoryError as error:
        await _handle_repo_error(
            message, lang=lang, i18n=i18n, error=error, excursion_id=excursion_id
        )
        return
    catalog_store.invalidate()
    data["draft_buffer"] = {}
    data["return_to_review"] = False
    await state.set_data(data)
    await _after_language_saved(
        message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        code=code,
    )


async def _after_language_saved(
    message: Message,
    *,
    state: FSMContext,
    lang: str,
    i18n: I18n,
    excursions: ExcursionRepository,
    code: str,
) -> None:
    data = await state.get_data()
    excursion_id = str(data.get("excursion_id") or "")
    source = str(data.get("source_lang") or "")
    notice = i18n.t(
        lang,
        "admin.lang_saved",
        lang_name=i18n.t(lang, f"admin.lang_name.{code}"),
    )
    if data.get("return_to_review") or (source and code != source):
        await _send_translate_review(
            message,
            state=state,
            lang=lang,
            i18n=i18n,
            excursions=excursions,
            excursion_id=excursion_id,
            notice=notice,
        )
        return
    if source and code == source:
        await _send_summary_after_source(
            message,
            state=state,
            lang=lang,
            i18n=i18n,
            excursions=excursions,
            excursion_id=excursion_id,
            notice=notice,
        )
        return
    await _send_lang_hub(
        message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        excursion_id=excursion_id,
        notice=notice,
    )


async def _send_translate_offer(
    message: Message,
    *,
    state: FSMContext,
    lang: str,
    i18n: I18n,
    excursions: ExcursionRepository,
    excursion_id: str,
    source_code: str,
    notice: str = "",
) -> None:
    excursion = await excursions.get(excursion_id)
    if excursion is None:
        await message.answer(i18n.t(lang, "admin.not_found"))
        return
    await _bind_wizard_excursion(state, excursion, wizard=True)
    await state.update_data(source_lang=source_code)
    await state.set_state(AdminExcursionForm.translate_offer)
    parts = []
    if notice:
        parts.append(notice)
    parts.append(i18n.t(lang, "admin.translate_offer", code=source_code.upper()))
    await message.answer(
        "\n".join(parts),
        reply_markup=admin_translate_offer_keyboard(
            lang, i18n, excursion_id=excursion.id
        ),
    )


async def _send_translate_review(
    message: Message,
    *,
    state: FSMContext,
    lang: str,
    i18n: I18n,
    excursions: ExcursionRepository,
    excursion_id: str,
    notice: str = "",
) -> None:
    excursion = await excursions.get(excursion_id)
    if excursion is None:
        await message.answer(i18n.t(lang, "admin.not_found"))
        return
    source = _resolve_source_language(excursion) or "ru"
    await _bind_wizard_excursion(state, excursion, wizard=True)
    await state.update_data(source_lang=source)
    await state.set_state(AdminExcursionForm.translate_review)
    title = _display_title(excursion, lang, i18n, lang)
    statuses = {code: _review_status_code(excursion, code) for code in SUPPORTED_LANGS}
    parts: list[str] = []
    if notice:
        parts.append(notice)
    parts.append(
        i18n.t(
            lang,
            "admin.translate_review_title",
            title=title,
            source=source.upper(),
        )
    )
    for code in SUPPORTED_LANGS:
        fill_key = (
            f"admin.lang_ready.{code}"
            if _translation_ready(excursion, code)
            else f"admin.lang_missing.{code}"
        )
        review = i18n.t(lang, f"admin.review.{statuses[code]}")
        parts.append(f"{i18n.t(lang, fill_key)} · {review}")
    await message.answer(
        "\n".join(parts),
        reply_markup=admin_translate_review_keyboard(
            lang,
            i18n,
            excursion_id=excursion.id,
            source_lang=source,
            statuses=statuses,
        ),
    )


async def _send_photo_stage(
    message: Message,
    *,
    state: FSMContext,
    lang: str,
    i18n: I18n,
    excursions: ExcursionRepository,
    excursion_id: str,
    notice: str = "",
    edit_mode: bool = False,
) -> None:
    excursion = await excursions.get(excursion_id)
    if excursion is None:
        await message.answer(i18n.t(lang, "admin.not_found"))
        return
    count = _photo_count(excursion)
    await state.set_state(AdminExcursionForm.photo)
    await state.update_data(
        excursion_id=excursion.id,
        wizard=not edit_mode,
        edit_mode=edit_mode,
    )
    parts: list[str] = []
    if notice:
        parts.append(notice)
    key = "admin.ask_edit_photo" if edit_mode else "admin.ask_photo"
    parts.append(i18n.t(lang, key, count=count))
    await message.answer(
        "\n".join(parts),
        reply_markup=admin_photo_stage_keyboard(
            lang, i18n, excursion_id=excursion.id, count=count
        ),
    )


async def _ensure_source_language(
    excursions: ExcursionRepository,
    excursion: Excursion,
) -> Excursion:
    resolved = _resolve_source_language(excursion)
    if resolved is None:
        return excursion
    if excursion.source_language == resolved:
        return excursion
    try:
        return await excursions.update_fields(
            excursion_id=excursion.id,
            expected_version=excursion.version,
            fields={"source_language": resolved},
            apply_immediately=True,
        )
    except ExcursionRepositoryError:
        refreshed = await excursions.get(excursion.id)
        return refreshed or excursion


async def _run_auto_translate(
    message: Message,
    *,
    state: FSMContext,
    lang: str,
    i18n: I18n,
    excursions: ExcursionRepository,
    admin_translation: AdminTranslationService | None,
    excursion_id: str,
    telegram_user_id: int,
    force_overwrite: bool = False,
) -> None:
    if admin_translation is None or not admin_translation.is_available():
        await message.answer(i18n.t(lang, "admin.translate_unavailable"))
        await _send_translate_review(
            message,
            state=state,
            lang=lang,
            i18n=i18n,
            excursions=excursions,
            excursion_id=excursion_id,
        )
        return
    excursion = await excursions.get(excursion_id)
    if excursion is None:
        await message.answer(i18n.t(lang, "admin.not_found"))
        return
    source = _resolve_source_language(excursion)
    if source is None or not _translation_ready(excursion, source):
        await message.answer(i18n.t(lang, "admin.translate_error"))
        return
    source_block = excursion.translations[source]
    fingerprint = fingerprint_translation_block(source_block)
    try:
        result = await admin_translation.translate_from_source(
            telegram_user_id=telegram_user_id,
            excursion_id=excursion_id,
            source_fingerprint=fingerprint,
            force_overwrite=force_overwrite,
        )
    except AIBusyError:
        await message.answer(i18n.t(lang, "admin.translate_busy"))
        return
    except AILimitError:
        await message.answer(i18n.t(lang, "admin.translate_limit"))
        await _send_translate_review(
            message,
            state=state,
            lang=lang,
            i18n=i18n,
            excursions=excursions,
            excursion_id=excursion_id,
        )
        return
    except AIValidationError as error:
        if error.code == "overwrite_confirmation_required":
            await state.set_state(AdminExcursionForm.confirm_retranslate)
            await state.update_data(excursion_id=excursion_id, source_fp=fingerprint)
            await message.answer(
                i18n.t(lang, "admin.translate_overwrite_confirm"),
                reply_markup=admin_retranslate_confirm_keyboard(
                    lang, i18n, excursion_id=excursion_id
                ),
            )
            return
        if error.code == "source_changed":
            await message.answer(i18n.t(lang, "admin.translate_source_changed"))
        else:
            await message.answer(i18n.t(lang, "admin.translate_error"))
        await _send_translate_review(
            message,
            state=state,
            lang=lang,
            i18n=i18n,
            excursions=excursions,
            excursion_id=excursion_id,
        )
        return
    except (AIConfigError, AIError):
        await message.answer(i18n.t(lang, "admin.translate_error"))
        await _send_translate_review(
            message,
            state=state,
            lang=lang,
            i18n=i18n,
            excursions=excursions,
            excursion_id=excursion_id,
        )
        return
    _ = result
    await _send_translate_review(
        message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        excursion_id=excursion_id,
        notice=i18n.t(lang, "admin.translate_done"),
    )


async def _finish_wizard_schedule(
    message: Message,
    *,
    state: FSMContext,
    lang: str,
    i18n: I18n,
    excursions: ExcursionRepository,
    sessions: SessionRepository,
) -> None:
    """После фото — шаг расписания, затем сводка."""
    data = await state.get_data()
    excursion_id = str(data.get("excursion_id", ""))
    excursion = await excursions.get(excursion_id) if excursion_id else None
    if excursion is None:
        await state.clear()
        await message.answer(i18n.t(lang, "admin.not_found"))
        return
    await _bind_wizard_excursion(state, excursion, wizard=True)
    from excursion_ai.handlers.admin_schedule import (
        RETURN_WIZARD,
        open_schedule_for_excursion,
    )

    await open_schedule_for_excursion(
        message,
        state=state,
        lang=lang,
        i18n=i18n,
        sessions=sessions,
        excursion_id=excursion.id,
        schedule_return=RETURN_WIZARD,
        notice=i18n.t(lang, "admin.schedule.wizard_intro"),
    )


async def _finish_wizard_summary(
    message: Message,
    *,
    state: FSMContext,
    lang: str,
    i18n: I18n,
    excursions: ExcursionRepository,
) -> None:
    data = await state.get_data()
    excursion_id = str(data.get("excursion_id", ""))
    excursion = await excursions.get(excursion_id) if excursion_id else None
    if excursion is None:
        await state.clear()
        await message.answer(i18n.t(lang, "admin.not_found"))
        return
    await _bind_wizard_excursion(state, excursion, wizard=True)
    await state.set_state(AdminExcursionForm.preview)
    await _send_summary(
        message,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        excursion_id=excursion.id,
    )


async def open_admin_home(
    message: Message,
    *,
    state: FSMContext,
    lang: str,
    i18n: I18n,
) -> None:
    await leave_consult_mode(state)
    await _send_home(message, lang=lang, i18n=i18n, state=state)
    log_event(logger, action="admin.ui", stage="home", result="ok")


@router.message(Command("admin"))
@router.message(MenuText("menu.admin"))
async def cmd_admin(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
) -> None:
    lang = await require_admin(message, settings, i18n, langs)
    if not lang:
        return
    ui_lang = await require_language(message, langs, i18n)
    if not ui_lang:
        return
    if await is_in_form(state):
        await ask_conflict(
            message,
            lang=ui_lang,
            i18n=i18n,
            state=state,
            pending_action="menu_admin",
        )
        return
    await open_admin_home(message, state=state, lang=ui_lang, i18n=i18n)


@router.callback_query(AdminCallback.filter(F.action == "menu"))
async def cb_menu(
    callback: CallbackQuery,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message) or not callback.from_user:
        return
    await state.clear()
    await callback.answer()
    await send_welcome(
        callback.message,
        lang,
        i18n,
        is_admin=settings.is_admin(callback.from_user.id),
    )


@router.callback_query(AdminCallback.filter(F.action == "home"))
async def cb_home(
    callback: CallbackQuery,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await callback.answer()
    await _send_home(callback.message, lang=lang, i18n=i18n, state=state)


@router.callback_query(AdminCallback.filter(F.action == "list"))
async def cb_list(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    status = callback_data.field or ExcursionStatus.DRAFT.value
    if status not in {s.value for s in ExcursionStatus}:
        await callback.answer()
        return
    await state.clear()
    await callback.answer()
    await _send_list(
        callback.message,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        status=status,
        page=callback_data.page,
    )


@router.callback_query(AdminCallback.filter(F.action == "view"))
async def cb_view(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await state.clear()
    await callback.answer()
    await _send_card(
        callback.message,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        excursion_id=callback_data.excursion_id,
        list_status=callback_data.field,
        page=callback_data.page,
    )


@router.callback_query(AdminCallback.filter(F.action == "create"))
async def cb_create(
    callback: CallbackQuery,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await state.clear()
    await state.set_state(AdminExcursionForm.city)
    await state.set_data({"wizard": True, "conducted": []})
    await callback.answer()
    await callback.message.answer(i18n.t(lang, "admin.create_intro"))
    await callback.message.answer(
        i18n.t(lang, "admin.ask_city"),
        reply_markup=admin_wizard_nav(lang, i18n),
    )
    log_event(logger, action="admin.ui", stage="create_start", result="ok")


@router.callback_query(AdminCallback.filter(F.action == "cancel"))
async def cb_cancel(
    callback: CallbackQuery,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    data = await state.get_data()
    excursion_id = str(data.get("excursion_id") or "")
    await state.clear()
    await callback.answer()
    await callback.message.answer(i18n.t(lang, "admin.cancelled"))
    if excursion_id:
        await _send_card(
            callback.message,
            lang=lang,
            i18n=i18n,
            excursions=excursions,
            excursion_id=excursion_id,
        )
        return
    await _send_home(callback.message, lang=lang, i18n=i18n, state=state)


@router.message(StateFilter(AdminExcursionForm.city))
async def wizard_city(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
    catalog_store: CatalogStore,
) -> None:
    lang = await require_admin(message, settings, i18n, langs)
    if not lang or not message.text:
        return
    city_id = _parse_city(message.text)
    if city_id is None:
        await message.answer(i18n.t(lang, "admin.error.city"))
        return
    excursion_id = f"{city_id}-{secrets.token_hex(4)}"
    try:
        await excursions.create_draft(
            excursion_id=excursion_id,
            city_id=city_id,
            duration_minutes=15,
            price_per_person=Decimal("0"),
            currency="EUR",
            conducted_languages=[],
        )
    except ExcursionRepositoryError as error:
        await _handle_repo_error(
            message, lang=lang, i18n=i18n, error=error, excursion_id=excursion_id
        )
        return
    catalog_store.invalidate()
    await state.update_data(
        excursion_id=excursion_id,
        city_id=city_id,
        wizard=True,
        conducted=[],
        lang_index=0,
        draft_buffer={},
    )
    await state.set_state(AdminExcursionForm.duration)
    await message.answer(
        i18n.t(lang, "admin.ask_duration"),
        reply_markup=admin_wizard_nav(lang, i18n),
    )


@router.message(StateFilter(AdminExcursionForm.duration))
async def wizard_duration(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
    catalog_store: CatalogStore,
) -> None:
    lang = await require_admin(message, settings, i18n, langs)
    if not lang or not message.text:
        return
    duration = _parse_duration(message.text)
    if duration is None:
        await message.answer(i18n.t(lang, "admin.error.duration"))
        return
    data = await state.get_data()
    excursion_id = str(data["excursion_id"])
    excursion = await excursions.get(excursion_id)
    if excursion is None:
        await message.answer(i18n.t(lang, "admin.not_found"))
        return
    try:
        await excursions.update_fields(
            excursion_id=excursion_id,
            expected_version=excursion.version,
            fields={"duration_minutes": duration},
            apply_immediately=True,
        )
    except ExcursionRepositoryError as error:
        await _handle_repo_error(
            message, lang=lang, i18n=i18n, error=error, excursion_id=excursion_id
        )
        return
    catalog_store.invalidate()
    await state.set_state(AdminExcursionForm.price)
    await message.answer(
        i18n.t(lang, "admin.ask_price"),
        reply_markup=admin_wizard_nav(lang, i18n),
    )


@router.message(StateFilter(AdminExcursionForm.price))
async def wizard_price(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
    catalog_store: CatalogStore,
) -> None:
    lang = await require_admin(message, settings, i18n, langs)
    if not lang or not message.text:
        return
    price = _parse_price(message.text)
    if price is None:
        await message.answer(i18n.t(lang, "admin.error.price"))
        return
    data = await state.get_data()
    excursion_id = str(data["excursion_id"])
    excursion = await excursions.get(excursion_id)
    if excursion is None:
        await message.answer(i18n.t(lang, "admin.not_found"))
        return
    try:
        await excursions.update_fields(
            excursion_id=excursion_id,
            expected_version=excursion.version,
            fields={"price_per_person": str(price), "currency": "EUR"},
            apply_immediately=True,
        )
    except ExcursionRepositoryError as error:
        await _handle_repo_error(
            message, lang=lang, i18n=i18n, error=error, excursion_id=excursion_id
        )
        return
    catalog_store.invalidate()
    conducted = list(data.get("conducted") or [])
    await state.set_state(AdminExcursionForm.languages)
    await message.answer(
        i18n.t(lang, "admin.ask_languages"),
        reply_markup=admin_lang_conducted(lang, i18n, conducted),
    )


@router.callback_query(
    StateFilter(AdminExcursionForm.languages),
    AdminCallback.filter(F.action == "lang_toggle"),
)
async def wizard_lang_toggle(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    code = callback_data.lang
    if code not in SUPPORTED_LANGS:
        await callback.answer()
        return
    data = await state.get_data()
    conducted = set(data.get("conducted") or [])
    if code in conducted:
        conducted.remove(code)
    else:
        conducted.add(code)
    data["conducted"] = sorted(conducted)
    await state.set_data(data)
    await callback.answer()
    await callback.message.edit_reply_markup(
        reply_markup=admin_lang_conducted(lang, i18n, conducted)
    )


@router.callback_query(
    StateFilter(AdminExcursionForm.languages),
    AdminCallback.filter(F.action == "lang_done"),
)
async def wizard_lang_done(
    callback: CallbackQuery,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
    catalog_store: CatalogStore,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    data = await state.get_data()
    conducted = list(data.get("conducted") or [])
    if not conducted:
        await callback.answer(i18n.t(lang, "admin.error.languages"), show_alert=True)
        return
    excursion_id = str(data["excursion_id"])
    excursion = await excursions.get(excursion_id)
    if excursion is None:
        await callback.answer()
        await callback.message.answer(i18n.t(lang, "admin.not_found"))
        return
    try:
        await excursions.update_fields(
            excursion_id=excursion_id,
            expected_version=excursion.version,
            fields={"conducted_languages": conducted},
            apply_immediately=_apply_immediate_flag(excursion.status)
            if data.get("edit_mode")
            else True,
        )
    except ExcursionRepositoryError as error:
        await callback.answer()
        await _handle_repo_error(
            callback.message, lang=lang, i18n=i18n, error=error, excursion_id=excursion_id
        )
        return
    catalog_store.invalidate()
    await callback.answer()
    if data.get("edit_mode"):
        await state.clear()
        notice = (
            i18n.t(lang, "admin.pending_saved")
            if excursion.status == ExcursionStatus.PUBLISHED.value
            else i18n.t(lang, "admin.saved")
        )
        await _send_card(
            callback.message,
            lang=lang,
            i18n=i18n,
            excursions=excursions,
            excursion_id=excursion_id,
            notice=notice,
        )
        return
    await state.set_state(AdminExcursionForm.source_lang)
    await callback.message.answer(i18n.t(lang, "admin.translations_intro"))
    await callback.message.answer(
        i18n.t(lang, "admin.ask_source_lang"),
        reply_markup=admin_source_lang_keyboard(lang, i18n),
    )


@router.message(StateFilter(AdminExcursionForm.title))
async def wizard_title(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
    catalog_store: CatalogStore,
) -> None:
    lang = await require_admin(message, settings, i18n, langs)
    if not lang or not message.text:
        return
    title = _parse_title(message.text)
    if title is None:
        await message.answer(i18n.t(lang, "admin.error.title"))
        return
    data = await state.get_data()
    code = _active_lang_code(data)
    buffer = dict(data.get("draft_buffer") or {})
    block = dict(buffer.get(code) or {})
    block["title"] = title
    buffer[code] = block
    data["draft_buffer"] = buffer
    data["active_lang"] = code
    await state.set_data(data)
    await _advance_text_wizard(
        message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        catalog_store=catalog_store,
        completed_step="title",
    )


@router.message(StateFilter(AdminExcursionForm.description))
async def wizard_description(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
    catalog_store: CatalogStore,
) -> None:
    lang = await require_admin(message, settings, i18n, langs)
    if not lang or not message.text:
        return
    description = _parse_description(message.text)
    if description is None:
        await message.answer(i18n.t(lang, "admin.error.description"))
        return
    data = await state.get_data()
    code = _active_lang_code(data)
    buffer = dict(data.get("draft_buffer") or {})
    block = dict(buffer.get(code) or {})
    block["description"] = description
    buffer[code] = block
    data["draft_buffer"] = buffer
    data["active_lang"] = code
    await state.set_data(data)
    await _advance_text_wizard(
        message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        catalog_store=catalog_store,
        completed_step="description",
    )


@router.message(StateFilter(AdminExcursionForm.route))
async def wizard_route(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
    catalog_store: CatalogStore,
) -> None:
    lang = await require_admin(message, settings, i18n, langs)
    if not lang or not message.text:
        return
    route = _parse_route(message.text)
    if route is None:
        await message.answer(i18n.t(lang, "admin.error.route"))
        return
    data = await state.get_data()
    code = _active_lang_code(data)
    buffer = dict(data.get("draft_buffer") or {})
    block = dict(buffer.get(code) or {})
    block["route"] = route
    buffer[code] = block
    data["draft_buffer"] = buffer
    data["active_lang"] = code
    await state.set_data(data)
    await _advance_text_wizard(
        message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        catalog_store=catalog_store,
        completed_step="route",
    )


@router.message(StateFilter(AdminExcursionForm.meeting))
async def wizard_meeting(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
    catalog_store: CatalogStore,
) -> None:
    lang = await require_admin(message, settings, i18n, langs)
    if not lang or not message.text:
        return
    meeting = _parse_meeting(message.text)
    if meeting is None:
        await message.answer(i18n.t(lang, "admin.error.meeting"))
        return
    data = await state.get_data()
    code = _active_lang_code(data)
    buffer = dict(data.get("draft_buffer") or {})
    block = dict(buffer.get(code) or {})
    block["meeting_point"] = meeting
    buffer[code] = block
    data["draft_buffer"] = buffer
    data["active_lang"] = code
    await state.set_data(data)
    await _advance_text_wizard(
        message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        catalog_store=catalog_store,
        completed_step="meeting",
    )


@router.callback_query(
    StateFilter(AdminExcursionForm.source_lang),
    AdminCallback.filter(F.action == "source_lang"),
)
async def wizard_source_lang(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
    catalog_store: CatalogStore,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    code = callback_data.lang
    if code not in SUPPORTED_LANGS:
        await callback.answer()
        return
    data = await state.get_data()
    excursion_id = str(data.get("excursion_id") or "")
    excursion = await excursions.get(excursion_id) if excursion_id else None
    if excursion is None:
        await callback.answer()
        await callback.message.answer(i18n.t(lang, "admin.not_found"))
        return
    try:
        await excursions.update_fields(
            excursion_id=excursion_id,
            expected_version=excursion.version,
            fields={"source_language": code},
            apply_immediately=True,
        )
    except ExcursionRepositoryError as error:
        await callback.answer()
        await _handle_repo_error(
            callback.message, lang=lang, i18n=i18n, error=error, excursion_id=excursion_id
        )
        return
    catalog_store.invalidate()
    await callback.answer()
    await state.update_data(
        source_lang=code,
        active_lang=code,
        lang_index=SUPPORTED_LANGS.index(code),
        draft_buffer={},
    )
    await callback.message.answer(
        i18n.t(lang, "admin.source_saved", code=code.upper())
    )
    excursion = await excursions.get(excursion_id)
    if excursion is None:
        await callback.message.answer(i18n.t(lang, "admin.not_found"))
        return
    first = _first_open_text_step(excursion, code, {}) or "title"
    await _prompt_translation_step(
        callback.message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        step=first,
    )


async def _persist_photos(
    message: Message,
    *,
    state: FSMContext,
    lang: str,
    i18n: I18n,
    excursions: ExcursionRepository,
    catalog_store: CatalogStore,
    photos: list[dict[str, str]],
) -> None:
    data = await state.get_data()
    excursion_id = str(data.get("excursion_id") or "")
    if not excursion_id or not photos:
        return
    current_state = await state.get_state()
    if current_state not in {
        AdminExcursionForm.photo.state,
        AdminExcursionForm.photo_manage.state,
        AdminExcursionForm.photo_replace.state,
    }:
        return
    excursion = await excursions.get(excursion_id)
    if excursion is None:
        await message.answer(i18n.t(lang, "admin.not_found"))
        return
    if current_state == AdminExcursionForm.photo_replace.state:
        index = int(data.get("replace_index", -1))
        ordered = list(excursion.photos)
        if index < 0 or index >= len(ordered):
            await message.answer(i18n.t(lang, "admin.error.photo"))
            return
        photo = photos[0]
        try:
            await excursions.replace_photo(
                excursion_id=excursion_id,
                expected_version=excursion.version,
                old_file_unique_id=ordered[index].file_unique_id,
                file_id=photo["file_id"],
                file_unique_id=photo["file_unique_id"],
                apply_immediately=_apply_immediate_flag(excursion.status)
                if data.get("edit_mode")
                else True,
            )
        except ExcursionRepositoryError as error:
            await _handle_repo_error(
                message, lang=lang, i18n=i18n, error=error, excursion_id=excursion_id
            )
            return
        catalog_store.invalidate()
        await _send_photo_stage(
            message,
            state=state,
            lang=lang,
            i18n=i18n,
            excursions=excursions,
            excursion_id=excursion_id,
            notice=i18n.t(lang, "admin.photo_replaced"),
            edit_mode=bool(data.get("edit_mode")),
        )
        return

    try:
        updated, added, rejected = await excursions.add_photos(
            excursion_id=excursion_id,
            expected_version=excursion.version,
            photos=photos[:MAX_PHOTOS],
            apply_immediately=_apply_immediate_flag(excursion.status)
            if data.get("edit_mode")
            else True,
        )
    except ExcursionRepositoryError as error:
        await _handle_repo_error(
            message, lang=lang, i18n=i18n, error=error, excursion_id=excursion_id
        )
        return
    catalog_store.invalidate()
    notices = [
        i18n.t(
            lang,
            "admin.photo_added",
            added=len(added),
            count=_photo_count(updated),
        )
    ]
    if any(r.get("reason") == "limit" for r in rejected):
        notices.append(i18n.t(lang, "admin.photo_rejected_limit"))
    if any(r.get("reason") == "duplicate" for r in rejected):
        notices.append(i18n.t(lang, "admin.photo_rejected_dup"))
    await _send_photo_stage(
        message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        excursion_id=excursion_id,
        notice="\n".join(notices),
        edit_mode=bool(data.get("edit_mode")),
    )


async def _schedule_media_group_flush(
    *,
    media_group_id: str,
    message: Message,
    state: FSMContext,
    lang: str,
    i18n: I18n,
    excursions: ExcursionRepository,
    catalog_store: CatalogStore,
) -> None:
    async def _flush() -> None:
        await asyncio.sleep(MEDIA_GROUP_DEBOUNCE_SEC)
        photos = _media_group_buffers.pop(media_group_id, [])
        _media_group_tasks.pop(media_group_id, None)
        meta = _media_group_meta.pop(media_group_id, {})
        if not photos:
            return
        data = await state.get_data()
        excursion_id = str(data.get("excursion_id") or "")
        if excursion_id != str(meta.get("excursion_id") or ""):
            return
        current_state = await state.get_state()
        if current_state not in {
            AdminExcursionForm.photo.state,
            AdminExcursionForm.photo_manage.state,
            AdminExcursionForm.photo_replace.state,
        }:
            return
        # dedupe by unique id, keep order, cap at MAX_PHOTOS
        seen: set[str] = set()
        unique_photos: list[dict[str, str]] = []
        for item in photos:
            uid = item["file_unique_id"]
            if uid in seen:
                continue
            seen.add(uid)
            unique_photos.append(item)
            if len(unique_photos) >= MAX_PHOTOS:
                break
        await _persist_photos(
            message,
            state=state,
            lang=lang,
            i18n=i18n,
            excursions=excursions,
            catalog_store=catalog_store,
            photos=unique_photos,
        )

    existing = _media_group_tasks.get(media_group_id)
    if existing and not existing.done():
        existing.cancel()
    _media_group_tasks[media_group_id] = asyncio.create_task(_flush())


@router.message(StateFilter(AdminExcursionForm.photo, AdminExcursionForm.photo_replace), F.photo)
async def wizard_photo(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
    catalog_store: CatalogStore,
) -> None:
    lang = await require_admin(message, settings, i18n, langs)
    if not lang or not message.photo:
        return
    data = await state.get_data()
    excursion_id = str(data.get("excursion_id") or "")
    photo = message.photo[-1]
    payload = {
        "file_id": photo.file_id,
        "file_unique_id": photo.file_unique_id,
    }
    media_group_id = message.media_group_id
    if media_group_id:
        bucket = _media_group_buffers.setdefault(media_group_id, [])
        bucket.append(payload)
        _media_group_meta[media_group_id] = {"excursion_id": excursion_id}
        await _schedule_media_group_flush(
            media_group_id=media_group_id,
            message=message,
            state=state,
            lang=lang,
            i18n=i18n,
            excursions=excursions,
            catalog_store=catalog_store,
        )
        return
    await _persist_photos(
        message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        catalog_store=catalog_store,
        photos=[payload],
    )


@router.message(StateFilter(AdminExcursionForm.photo, AdminExcursionForm.photo_replace))
async def wizard_photo_invalid(
    message: Message,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
) -> None:
    lang = await require_admin(message, settings, i18n, langs)
    if not lang:
        return
    await message.answer(i18n.t(lang, "admin.error.photo"))


@router.callback_query(AdminCallback.filter(F.action.in_({"photo_skip", "skip_photo"})))
async def cb_photo_skip(
    callback: CallbackQuery,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
    sessions: SessionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    data = await state.get_data()
    excursion_id = str(data.get("excursion_id") or "")
    edit_mode = bool(data.get("edit_mode"))
    await callback.answer()
    if edit_mode and excursion_id:
        await state.clear()
        await _send_card(
            callback.message,
            lang=lang,
            i18n=i18n,
            excursions=excursions,
            excursion_id=excursion_id,
            notice=i18n.t(lang, "admin.saved"),
            sessions=sessions,
        )
        return
    await _finish_wizard_schedule(
        callback.message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        sessions=sessions,
    )


@router.callback_query(AdminCallback.filter(F.action == "photo_done"))
async def cb_photo_done(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
    sessions: SessionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    data = await state.get_data()
    excursion_id = callback_data.excursion_id or str(data.get("excursion_id") or "")
    await callback.answer()
    if data.get("edit_mode") and excursion_id:
        await state.clear()
        await _send_card(
            callback.message,
            lang=lang,
            i18n=i18n,
            excursions=excursions,
            excursion_id=excursion_id,
            notice=i18n.t(lang, "admin.saved"),
            sessions=sessions,
        )
        return
    if excursion_id:
        await state.update_data(excursion_id=excursion_id)
    await _finish_wizard_schedule(
        callback.message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        sessions=sessions,
    )


@router.callback_query(AdminCallback.filter(F.action == "photo_stage"))
async def cb_photo_stage(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await callback.answer()
    await _send_photo_stage(
        callback.message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        excursion_id=callback_data.excursion_id,
    )


@router.callback_query(AdminCallback.filter(F.action == "photo_manage"))
async def cb_photo_manage(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    excursion = await excursions.get(callback_data.excursion_id)
    if excursion is None:
        await callback.answer()
        await callback.message.answer(i18n.t(lang, "admin.not_found"))
        return
    await callback.answer()
    await state.set_state(AdminExcursionForm.photo_manage)
    await state.update_data(excursion_id=excursion.id)
    photos = [
        (index, photo.file_unique_id)
        for index, photo in enumerate(excursion.photos)
    ]
    await callback.message.answer(
        i18n.t(lang, "admin.photo_manage_title", count=len(photos)),
        reply_markup=admin_photo_manage_keyboard(
            lang, i18n, excursion_id=excursion.id, photos=photos
        ),
    )


@router.callback_query(AdminCallback.filter(F.action == "photo_back"))
async def cb_photo_back(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    data = await state.get_data()
    await callback.answer()
    await _send_photo_stage(
        callback.message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        excursion_id=callback_data.excursion_id or str(data.get("excursion_id") or ""),
        edit_mode=bool(data.get("edit_mode")),
    )


@router.callback_query(AdminCallback.filter(F.action == "photo_delete"))
async def cb_photo_delete(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
    catalog_store: CatalogStore,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    excursion = await excursions.get(callback_data.excursion_id)
    if excursion is None:
        await callback.answer()
        await callback.message.answer(i18n.t(lang, "admin.not_found"))
        return
    index = callback_data.page
    ordered = list(excursion.photos)
    if index < 0 or index >= len(ordered):
        await callback.answer(i18n.t(lang, "admin.stale_action"), show_alert=True)
        return
    data = await state.get_data()
    try:
        updated = await excursions.delete_photo(
            excursion_id=excursion.id,
            expected_version=excursion.version,
            file_unique_id=ordered[index].file_unique_id,
            apply_immediately=_apply_immediate_flag(excursion.status)
            if data.get("edit_mode")
            else True,
        )
    except ExcursionRepositoryError as error:
        await callback.answer()
        await _handle_repo_error(
            callback.message, lang=lang, i18n=i18n, error=error, excursion_id=excursion.id
        )
        return
    catalog_store.invalidate()
    await callback.answer()
    await _send_photo_stage(
        callback.message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        excursion_id=updated.id,
        notice=i18n.t(lang, "admin.photo_deleted", count=_photo_count(updated)),
        edit_mode=bool(data.get("edit_mode")),
    )


@router.callback_query(AdminCallback.filter(F.action == "photo_cover"))
async def cb_photo_cover(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
    catalog_store: CatalogStore,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    excursion = await excursions.get(callback_data.excursion_id)
    if excursion is None:
        await callback.answer()
        await callback.message.answer(i18n.t(lang, "admin.not_found"))
        return
    index = callback_data.page
    ordered = list(excursion.photos)
    if index < 0 or index >= len(ordered):
        await callback.answer(i18n.t(lang, "admin.stale_action"), show_alert=True)
        return
    data = await state.get_data()
    try:
        await excursions.set_cover_photo(
            excursion_id=excursion.id,
            expected_version=excursion.version,
            file_unique_id=ordered[index].file_unique_id,
            apply_immediately=_apply_immediate_flag(excursion.status)
            if data.get("edit_mode")
            else True,
        )
    except ExcursionRepositoryError as error:
        await callback.answer()
        await _handle_repo_error(
            callback.message, lang=lang, i18n=i18n, error=error, excursion_id=excursion.id
        )
        return
    catalog_store.invalidate()
    await callback.answer()
    await _send_photo_stage(
        callback.message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        excursion_id=excursion.id,
        notice=i18n.t(lang, "admin.photo_cover_set"),
        edit_mode=bool(data.get("edit_mode")),
    )


@router.callback_query(AdminCallback.filter(F.action == "photo_replace"))
async def cb_photo_replace(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    excursion = await excursions.get(callback_data.excursion_id)
    if excursion is None:
        await callback.answer()
        await callback.message.answer(i18n.t(lang, "admin.not_found"))
        return
    index = callback_data.page
    if index < 0 or index >= len(excursion.photos):
        await callback.answer(i18n.t(lang, "admin.stale_action"), show_alert=True)
        return
    data = await state.get_data()
    await callback.answer()
    await state.set_state(AdminExcursionForm.photo_replace)
    await state.update_data(
        excursion_id=excursion.id,
        replace_index=index,
        edit_mode=bool(data.get("edit_mode")),
    )
    await callback.message.answer(
        i18n.t(lang, "admin.ask_photo_replace", n=index + 1),
        reply_markup=admin_wizard_nav(lang, i18n, excursion_id=excursion.id),
    )


@router.callback_query(AdminCallback.filter(F.action == "translate_auto"))
async def cb_translate_auto(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
    admin_translation: AdminTranslationService | None = None,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message) or not callback.from_user:
        return
    await callback.answer()
    await _run_auto_translate(
        callback.message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        admin_translation=admin_translation,
        excursion_id=callback_data.excursion_id,
        telegram_user_id=callback.from_user.id,
        force_overwrite=False,
    )


@router.callback_query(AdminCallback.filter(F.action == "translate_redo"))
async def cb_translate_redo(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
    admin_translation: AdminTranslationService | None = None,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message) or not callback.from_user:
        return
    await callback.answer()
    await _run_auto_translate(
        callback.message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        admin_translation=admin_translation,
        excursion_id=callback_data.excursion_id,
        telegram_user_id=callback.from_user.id,
        force_overwrite=False,
    )


@router.callback_query(AdminCallback.filter(F.action == "translate_redo_confirm"))
async def cb_translate_redo_confirm(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
    admin_translation: AdminTranslationService | None = None,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message) or not callback.from_user:
        return
    await callback.answer()
    await _run_auto_translate(
        callback.message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        admin_translation=admin_translation,
        excursion_id=callback_data.excursion_id,
        telegram_user_id=callback.from_user.id,
        force_overwrite=True,
    )


@router.callback_query(AdminCallback.filter(F.action == "translate_manual"))
async def cb_translate_manual(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await callback.answer()
    await _send_translate_review(
        callback.message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        excursion_id=callback_data.excursion_id,
    )


@router.callback_query(AdminCallback.filter(F.action == "translate_view"))
async def cb_translate_view(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    code = callback_data.lang
    if code not in SUPPORTED_LANGS:
        await callback.answer()
        return
    excursion = await excursions.get(callback_data.excursion_id)
    if excursion is None:
        await callback.answer()
        await callback.message.answer(i18n.t(lang, "admin.not_found"))
        return
    block = excursion.translations.get(code) or {}
    route = block.get("route") or []
    route_lines = (
        "\n".join(f"{i}. {item}" for i, item in enumerate(route, start=1))
        if isinstance(route, list)
        else "—"
    )
    body = "\n".join(
        [
            str(block.get("title") or "—"),
            str(block.get("description") or "—"),
            route_lines,
            str(block.get("meeting_point") or "—"),
        ]
    )
    await callback.answer()
    await callback.message.answer(
        i18n.t(lang, "admin.translate_view", code=code.upper(), body=body)
    )


@router.callback_query(AdminCallback.filter(F.action == "translate_confirm"))
async def cb_translate_confirm(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
    catalog_store: CatalogStore,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    code = callback_data.lang
    if code not in SUPPORTED_LANGS:
        await callback.answer()
        return
    excursion = await excursions.get(callback_data.excursion_id)
    if excursion is None:
        await callback.answer()
        await callback.message.answer(i18n.t(lang, "admin.not_found"))
        return
    if not _translation_ready(excursion, code):
        await callback.answer(i18n.t(lang, "admin.stale_action"), show_alert=True)
        return
    try:
        await excursions.set_translation_review(
            excursion_id=excursion.id,
            language=code,
            review_status=REVIEW_CONFIRMED,
            expected_version=excursion.version,
        )
    except ExcursionRepositoryError as error:
        await callback.answer()
        await _handle_repo_error(
            callback.message, lang=lang, i18n=i18n, error=error, excursion_id=excursion.id
        )
        return
    catalog_store.invalidate()
    await callback.answer()
    await _send_translate_review(
        callback.message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        excursion_id=excursion.id,
        notice=i18n.t(lang, "admin.translate_confirmed", code=code.upper()),
    )


@router.callback_query(AdminCallback.filter(F.action == "translate_edit"))
async def cb_translate_edit(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    code = callback_data.lang
    if code not in SUPPORTED_LANGS:
        await callback.answer()
        return
    excursion = await excursions.get(callback_data.excursion_id)
    if excursion is None:
        await callback.answer()
        await callback.message.answer(i18n.t(lang, "admin.not_found"))
        return
    source = _resolve_source_language(excursion) or code
    await callback.answer()
    if _translation_ready(excursion, code):
        await callback.message.answer(
            i18n.t(lang, "admin.edit_translation", code=code.upper()),
            reply_markup=admin_edit_translation_keyboard(
                lang,
                i18n,
                excursion_id=excursion.id,
                target_lang=code,
            ),
        )
        return
    await _bind_wizard_excursion(state, excursion, wizard=True)
    await state.update_data(
        active_lang=code,
        source_lang=source,
        lang_index=SUPPORTED_LANGS.index(code),
        draft_buffer={},
        return_to_review=True,
    )
    first = _first_open_text_step(excursion, code, {}) or "title"
    await _prompt_translation_step(
        callback.message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        step=first,
    )


def _apply_immediate_flag(status: str) -> bool:
    return status != ExcursionStatus.PUBLISHED.value


@router.callback_query(AdminCallback.filter(F.action == "lang_hub"))
async def cb_lang_hub(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await callback.answer()
    await _send_lang_hub(
        callback.message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        excursion_id=callback_data.excursion_id,
    )


@router.callback_query(AdminCallback.filter(F.action == "fill_lang"))
async def cb_fill_lang(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    code = callback_data.lang
    if code not in SUPPORTED_LANGS:
        await callback.answer(i18n.t(lang, "admin.stale_action"), show_alert=True)
        return
    excursion = await excursions.get(callback_data.excursion_id)
    if excursion is None:
        await callback.answer()
        await callback.message.answer(i18n.t(lang, "admin.not_found"))
        return
    await callback.answer()
    await _bind_wizard_excursion(state, excursion, wizard=True)
    await state.update_data(
        active_lang=code,
        lang_index=SUPPORTED_LANGS.index(code),
        draft_buffer={},
    )
    first = _first_open_text_step(excursion, code, {}) or "title"
    await _prompt_translation_step(
        callback.message,
        state=state,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        step=first,
    )


@router.callback_query(AdminCallback.filter(F.action == "summary"))
async def cb_summary(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await callback.answer()
    excursion = await excursions.get(callback_data.excursion_id)
    if excursion is None:
        await callback.message.answer(i18n.t(lang, "admin.not_found"))
        return
    await _bind_wizard_excursion(state, excursion, wizard=True)
    await state.set_state(AdminExcursionForm.lang_hub)
    await _send_summary(
        callback.message,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        excursion_id=excursion.id,
    )


@router.callback_query(AdminCallback.filter(F.action == "preview"))
async def cb_preview(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await callback.answer()
    await _send_preview(
        callback.message,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        excursion_id=callback_data.excursion_id,
    )


@router.callback_query(AdminCallback.filter(F.action == "continue_draft"))
async def cb_continue_draft(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
    catalog_store: CatalogStore,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await callback.answer()
    excursion = await excursions.get(callback_data.excursion_id)
    if excursion is None:
        await callback.message.answer(i18n.t(lang, "admin.not_found"))
        return
    before = excursion.source_language
    excursion = await _ensure_source_language(excursions, excursion)
    if excursion.source_language != before:
        catalog_store.invalidate()
    title = _display_title(excursion, lang, i18n, lang)
    notice = i18n.t(lang, "admin.continue_draft", title=title)
    source = _resolve_source_language(excursion)
    if source is None:
        await state.set_state(AdminExcursionForm.source_lang)
        await state.update_data(excursion_id=excursion.id, wizard=True, conducted=[])
        await callback.message.answer(notice)
        await callback.message.answer(
            i18n.t(lang, "admin.ask_source_lang"),
            reply_markup=admin_source_lang_keyboard(lang, i18n),
        )
        return
    if not _translation_ready(excursion, source):
        await _bind_wizard_excursion(state, excursion, wizard=True)
        await state.update_data(
            source_lang=source,
            active_lang=source,
            lang_index=SUPPORTED_LANGS.index(source),
            draft_buffer={},
        )
        await callback.message.answer(notice)
        first = _first_open_text_step(excursion, source, {}) or "title"
        await _prompt_translation_step(
            callback.message,
            state=state,
            lang=lang,
            i18n=i18n,
            excursions=excursions,
            step=first,
        )
        return
    all_confirmed = all(
        _translation_ready(excursion, code)
        and _review_status_code(excursion, code) == REVIEW_CONFIRMED
        for code in SUPPORTED_LANGS
    )
    if not all_confirmed:
        # If only source is ready, offer auto/manual; otherwise review hub.
        targets_ready = any(
            _translation_ready(excursion, code)
            for code in other_languages(source)
        )
        if not targets_ready:
            await _send_summary_after_source(
                callback.message,
                state=state,
                lang=lang,
                i18n=i18n,
                excursions=excursions,
                excursion_id=excursion.id,
                notice=notice,
            )
            return
        await _send_translate_review(
            callback.message,
            state=state,
            lang=lang,
            i18n=i18n,
            excursions=excursions,
            excursion_id=excursion.id,
            notice=notice,
        )
        return
    await _send_summary(
        callback.message,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        excursion_id=excursion.id,
        notice=notice,
    )


@router.callback_query(AdminCallback.filter(F.action == "save_draft_exit"))
async def cb_save_draft_exit(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await callback.answer()
    data = await state.get_data()
    excursion_id = callback_data.excursion_id or str(data.get("excursion_id") or "")
    if not excursion_id:
        await callback.message.answer(i18n.t(lang, "admin.stale_action"))
        return
    excursion = await excursions.get(excursion_id)
    if excursion is None:
        await callback.message.answer(i18n.t(lang, "admin.draft_save_failed"))
        return
    if excursion.status != ExcursionStatus.DRAFT.value:
        await state.clear()
        await callback.message.answer(i18n.t(lang, "admin.stale_action"))
        await _send_home(callback.message, lang=lang, i18n=i18n, state=state)
        return
    title = _display_title(excursion, lang, i18n, lang)
    await state.clear()
    await callback.message.answer(
        i18n.t(lang, "admin.draft_saved_exit", title=title)
    )
    await _send_home(callback.message, lang=lang, i18n=i18n, state=state)
    log_event(
        logger,
        action="admin.excursion",
        stage="save_draft_exit",
        result="ok",
        excursion_id=excursion_id,
    )


@router.callback_query(AdminCallback.filter(F.action == "view_catalog"))
async def cb_view_catalog(
    callback: CallbackQuery,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    catalog: Catalog,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await state.clear()
    await callback.answer()
    await send_catalog_list(callback.message, lang, i18n, catalog)


def _current_field_display(
    excursion: Excursion,
    *,
    field: str,
    target_lang: str,
    ui_lang: str,
    i18n: I18n,
) -> str:
    if field == "city_id":
        return excursion.city_id
    if field == "duration":
        return str(excursion.duration_minutes)
    if field == "price":
        return f"{excursion.price_per_person} {excursion.currency}"
    if field == "languages":
        return ",".join(excursion.conducted_languages) or "—"
    code = target_lang if target_lang in SUPPORTED_LANGS else ui_lang
    block = excursion.translations.get(code) or {}
    if field == "title":
        return str(block.get("title") or "—")
    if field == "description":
        return str(block.get("description") or "—")
    if field == "meeting":
        return str(block.get("meeting_point") or "—")
    if field == "city_label":
        return str(block.get("city") or "—")
    if field == "route":
        route = block.get("route") or []
        if not isinstance(route, list) or not route:
            return "—"
        return "\n".join(f"{idx}. {item}" for idx, item in enumerate(route, start=1))
    return "—"


async def _send_edit_hub(
    message: Message,
    *,
    lang: str,
    i18n: I18n,
    excursions: ExcursionRepository,
    excursion_id: str,
) -> None:
    excursion = await excursions.get(excursion_id)
    if excursion is None:
        await message.answer(i18n.t(lang, "admin.not_found"))
        return
    title = _display_title(excursion, lang, i18n, lang)
    await message.answer(
        i18n.t(
            lang,
            "admin.edit_hub",
            title=title,
            status=excursion.status,
            id=excursion.id,
        ),
        reply_markup=admin_edit_menu_keyboard(lang, i18n, excursion_id=excursion.id),
    )


@router.callback_query(AdminCallback.filter(F.action == "edit_menu"))
async def cb_edit_menu(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await state.clear()
    await callback.answer()
    await _send_edit_hub(
        callback.message,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        excursion_id=callback_data.excursion_id,
    )


@router.callback_query(AdminCallback.filter(F.action == "edit_section"))
async def cb_edit_section(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    section = callback_data.field
    excursion_id = callback_data.excursion_id
    excursion = await excursions.get(excursion_id)
    if excursion is None:
        await callback.answer()
        await callback.message.answer(i18n.t(lang, "admin.not_found"))
        return
    await callback.answer()
    if section == "photo":
        await _send_photo_stage(
            callback.message,
            state=state,
            lang=lang,
            i18n=i18n,
            excursions=excursions,
            excursion_id=excursion_id,
            edit_mode=True,
        )
        return
    if section == "route":
        lines = [i18n.t(lang, "admin.edit_route_intro")]
        for code in SUPPORTED_LANGS:
            current = _current_field_display(
                excursion,
                field="route",
                target_lang=code,
                ui_lang=lang,
                i18n=i18n,
            )
            lines.append(f"\n<b>{code.upper()}</b>\n{html.escape(current)}")
        await callback.message.answer(
            "\n".join(lines),
            reply_markup=admin_edit_route_keyboard(
                lang, i18n, excursion_id=excursion_id
            ),
            parse_mode="HTML",
        )
        return
    # data
    source = _resolve_source_language(excursion) or lang
    lines = [
        i18n.t(lang, "admin.edit_data_intro"),
        "",
        i18n.t(
            lang,
            "admin.edit_current_block",
            city_id=excursion.city_id,
            duration=excursion.duration_minutes,
            price=excursion.price_per_person,
            currency=excursion.currency,
            langs=",".join(excursion.conducted_languages) or "—",
        ),
        "",
        i18n.t(
            lang,
            "admin.edit_current_texts",
            code=source.upper(),
            title=html.escape(
                _current_field_display(
                    excursion, field="title", target_lang=source, ui_lang=lang, i18n=i18n
                )
            ),
            description=html.escape(
                _current_field_display(
                    excursion,
                    field="description",
                    target_lang=source,
                    ui_lang=lang,
                    i18n=i18n,
                )[:400]
            ),
            meeting=html.escape(
                _current_field_display(
                    excursion,
                    field="meeting",
                    target_lang=source,
                    ui_lang=lang,
                    i18n=i18n,
                )
            ),
        ),
    ]
    await callback.message.answer(
        "\n".join(lines),
        reply_markup=admin_edit_data_keyboard(lang, i18n, excursion_id=excursion_id),
        parse_mode="HTML",
    )


@router.callback_query(AdminCallback.filter(F.action == "edit_translation"))
async def cb_edit_translation(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    target = callback_data.lang
    if target not in SUPPORTED_LANGS:
        await callback.answer()
        return
    excursion = await excursions.get(callback_data.excursion_id)
    if excursion is None:
        await callback.answer()
        await callback.message.answer(i18n.t(lang, "admin.not_found"))
        return
    await callback.answer()
    current_title = _current_field_display(
        excursion, field="title", target_lang=target, ui_lang=lang, i18n=i18n
    )
    current_desc = _current_field_display(
        excursion, field="description", target_lang=target, ui_lang=lang, i18n=i18n
    )
    current_meeting = _current_field_display(
        excursion, field="meeting", target_lang=target, ui_lang=lang, i18n=i18n
    )
    await callback.message.answer(
        i18n.t(
            lang,
            "admin.edit_translation_current",
            code=target.upper(),
            title=html.escape(current_title),
            description=html.escape(current_desc[:400]),
            meeting=html.escape(current_meeting),
        ),
        reply_markup=admin_edit_translation_keyboard(
            lang,
            i18n,
            excursion_id=callback_data.excursion_id,
            target_lang=target,
        ),
        parse_mode="HTML",
    )


@router.callback_query(AdminCallback.filter(F.action == "edit_field"))
async def cb_edit_field(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    excursion_id = callback_data.excursion_id
    field = callback_data.field
    target_lang = callback_data.lang
    excursion = await excursions.get(excursion_id)
    if excursion is None:
        await callback.answer()
        await callback.message.answer(i18n.t(lang, "admin.not_found"))
        return
    current = await state.get_state()
    wizard_data = await state.get_data()
    if (
        current
        in {
            AdminExcursionForm.title.state,
            AdminExcursionForm.description.state,
            AdminExcursionForm.route.state,
            AdminExcursionForm.meeting.state,
        }
        and wizard_data.get("wizard")
        and not wizard_data.get("edit_mode")
    ):
        await callback.answer(i18n.t(lang, "admin.wizard_busy"), show_alert=True)
        return
    await callback.answer()
    if field == "languages":
        await state.set_state(AdminExcursionForm.languages)
        await state.set_data(
            {
                "excursion_id": excursion_id,
                "edit_mode": True,
                "conducted": list(excursion.conducted_languages),
            }
        )
        await callback.message.answer(
            i18n.t(lang, "admin.ask_edit_languages"),
            reply_markup=admin_lang_conducted(
                lang, i18n, list(excursion.conducted_languages)
            ),
        )
        return
    if field == "photo":
        await _send_photo_stage(
            callback.message,
            state=state,
            lang=lang,
            i18n=i18n,
            excursions=excursions,
            excursion_id=excursion_id,
            edit_mode=True,
        )
        return
    field_key = {
        "city_id": "admin.field.city_id",
        "duration": "admin.field.duration",
        "price": "admin.field.price",
        "title": "admin.field.title",
        "description": "admin.field.description",
        "route": "admin.field.route",
        "meeting": "admin.field.meeting",
        "city_label": "admin.field.city_label",
    }.get(field, "admin.field.city_id")
    current_value = _current_field_display(
        excursion,
        field=field,
        target_lang=target_lang,
        ui_lang=lang,
        i18n=i18n,
    )
    await state.set_state(AdminExcursionForm.edit_field)
    await state.set_data(
        {
            "excursion_id": excursion_id,
            "edit_mode": True,
            "edit_field": field,
            "edit_lang": target_lang,
            "expected_version": excursion.version,
            "edit_current": current_value,
        }
    )
    prompt = i18n.t(lang, "admin.ask_edit", field=i18n.t(lang, field_key))
    if field == "city_id":
        prompt = i18n.t(lang, "admin.ask_city")
    elif field == "duration":
        prompt = i18n.t(lang, "admin.ask_duration")
    elif field == "price":
        prompt = i18n.t(lang, "admin.ask_price")
    elif field in {"title", "description", "route", "meeting"} and target_lang:
        step = field if field != "meeting" else "meeting"
        title_hint = _title_hint_for_prompt(
            excursion,
            target_lang,
            {},
            ui_lang=lang,
            i18n=i18n,
        )
        prompt = _format_admin_text_field_prompt(
            lang,
            i18n,
            step,
            title_hint=title_hint,
            target_code=target_lang,
        )
    await callback.message.answer(
        i18n.t(
            lang,
            "admin.edit_current_value",
            field=i18n.t(lang, field_key),
            value=current_value,
        )
    )
    await callback.message.answer(prompt, reply_markup=admin_wizard_nav(lang, i18n))


@router.message(StateFilter(AdminExcursionForm.edit_field))
async def process_edit_field(
    message: Message,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
) -> None:
    lang = await require_admin(message, settings, i18n, langs)
    if not lang or not message.text:
        return
    data = await state.get_data()
    excursion_id = str(data["excursion_id"])
    field = str(data.get("edit_field") or "")
    target_lang = str(data.get("edit_lang") or "")
    excursion = await excursions.get(excursion_id)
    if excursion is None:
        await message.answer(i18n.t(lang, "admin.not_found"))
        await state.clear()
        return

    pending: dict[str, Any] = {"kind": field}
    preview_new = ""
    if field == "city_id":
        city_id = _parse_city(message.text)
        if city_id is None:
            await message.answer(i18n.t(lang, "admin.error.city"))
            return
        pending["city_id"] = city_id
        preview_new = city_id
    elif field == "duration":
        duration = _parse_duration(message.text)
        if duration is None:
            await message.answer(i18n.t(lang, "admin.error.duration"))
            return
        pending["duration_minutes"] = duration
        preview_new = str(duration)
    elif field == "price":
        price = _parse_price(message.text)
        if price is None:
            await message.answer(i18n.t(lang, "admin.error.price"))
            return
        pending["price_per_person"] = str(price)
        preview_new = f"{price} {excursion.currency}"
    elif field in {"title", "description", "route", "meeting", "city_label"}:
        if target_lang not in SUPPORTED_LANGS:
            await message.answer(i18n.t(lang, "admin.error.generic"))
            return
        block = dict(
            excursion.translations.get(target_lang)
            or {
                "title": "",
                "city": excursion.city_id,
                "description": "",
                "route": [],
                "meeting_point": "",
            }
        )
        if field == "title":
            value = _parse_title(message.text)
            if value is None:
                await message.answer(i18n.t(lang, "admin.error.title"))
                return
            block["title"] = value
            preview_new = value
        elif field == "description":
            value = _parse_description(message.text)
            if value is None:
                await message.answer(i18n.t(lang, "admin.error.description"))
                return
            block["description"] = value
            preview_new = value
        elif field == "route":
            route = _parse_route(message.text)
            if route is None:
                await message.answer(i18n.t(lang, "admin.error.route"))
                return
            block["route"] = route
            preview_new = "\n".join(
                f"{idx}. {item}" for idx, item in enumerate(route, start=1)
            )
        elif field == "meeting":
            value = _parse_meeting(message.text)
            if value is None:
                await message.answer(i18n.t(lang, "admin.error.meeting"))
                return
            block["meeting_point"] = value
            preview_new = value
        else:
            value = message.text.strip()
            if not (2 <= len(value) <= 128):
                await message.answer(i18n.t(lang, "admin.error.city"))
                return
            block["city"] = value
            preview_new = value
        pending.update(
            {
                "kind": "translation",
                "language": target_lang,
                "title": str(block.get("title", "")),
                "city": str(block.get("city", excursion.city_id)),
                "description": str(block.get("description", "")),
                "route": list(block.get("route") or []),
                "meeting_point": str(block.get("meeting_point", "")),
                "field": field,
            }
        )
    else:
        await message.answer(i18n.t(lang, "admin.error.generic"))
        return

    field_key = {
        "city_id": "admin.field.city_id",
        "duration": "admin.field.duration",
        "price": "admin.field.price",
        "title": "admin.field.title",
        "description": "admin.field.description",
        "route": "admin.field.route",
        "meeting": "admin.field.meeting",
        "city_label": "admin.field.city_label",
    }.get(field, "admin.field.city_id")
    await state.update_data(pending_edit=pending)
    await state.set_state(AdminExcursionForm.edit_confirm)
    await message.answer(
        i18n.t(
            lang,
            "admin.edit_preview",
            field=i18n.t(lang, field_key),
            current=str(data.get("edit_current") or "—"),
            new=preview_new,
        ),
        reply_markup=admin_edit_confirm_keyboard(
            lang, i18n, excursion_id=excursion_id
        ),
    )


@router.callback_query(AdminCallback.filter(F.action == "edit_confirm_save"))
async def cb_edit_confirm_save(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
    catalog_store: CatalogStore,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    data = await state.get_data()
    pending = data.get("pending_edit")
    excursion_id = callback_data.excursion_id or str(data.get("excursion_id") or "")
    if not isinstance(pending, dict) or not excursion_id:
        await callback.answer()
        await callback.message.answer(i18n.t(lang, "admin.stale_action"))
        await state.clear()
        return
    excursion = await excursions.get(excursion_id)
    if excursion is None:
        await callback.answer()
        await callback.message.answer(i18n.t(lang, "admin.not_found"))
        await state.clear()
        return
    await callback.answer()
    immediate = _apply_immediate_flag(excursion.status)
    kind = str(pending.get("kind") or "")
    try:
        if kind == "city_id":
            await excursions.update_fields(
                excursion_id=excursion_id,
                expected_version=excursion.version,
                fields={"city_id": pending["city_id"]},
                apply_immediately=immediate,
            )
        elif kind == "duration":
            await excursions.update_fields(
                excursion_id=excursion_id,
                expected_version=excursion.version,
                fields={"duration_minutes": int(pending["duration_minutes"])},
                apply_immediately=immediate,
            )
        elif kind == "price":
            await excursions.update_fields(
                excursion_id=excursion_id,
                expected_version=excursion.version,
                fields={"price_per_person": str(pending["price_per_person"])},
                apply_immediately=immediate,
            )
        elif kind == "translation":
            await excursions.upsert_translation(
                excursion_id=excursion_id,
                language=str(pending["language"]),
                title=str(pending.get("title", "")),
                city_label=str(pending.get("city", excursion.city_id)),
                description=str(pending.get("description", "")),
                route=list(pending.get("route") or []),
                meeting_point=str(pending.get("meeting_point", "")),
                expected_version=excursion.version,
                apply_immediately=immediate,
            )
        else:
            await callback.message.answer(i18n.t(lang, "admin.error.generic"))
            return
    except ExcursionRepositoryError as error:
        await _handle_repo_error(
            callback.message,
            lang=lang,
            i18n=i18n,
            error=error,
            excursion_id=excursion_id,
        )
        return
    catalog_store.invalidate()
    await state.clear()
    notice = (
        i18n.t(lang, "admin.pending_saved")
        if not immediate
        else i18n.t(lang, "admin.saved")
    )
    await _send_card(
        callback.message,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        excursion_id=excursion_id,
        notice=notice,
    )


@router.callback_query(AdminCallback.filter(F.action == "edit_confirm_cancel"))
async def cb_edit_confirm_cancel(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await callback.answer()
    await state.clear()
    await callback.message.answer(i18n.t(lang, "admin.edit_discarded"))
    await _send_edit_hub(
        callback.message,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        excursion_id=callback_data.excursion_id,
    )


async def _missing_for_publish(
    excursion: Excursion,
    *,
    lang: str,
    i18n: I18n,
    sessions: SessionRepository,
) -> list[str]:
    missing = _missing_required(excursion, lang=lang, i18n=i18n)
    if await sessions.count_active(excursion.id) < 1:
        missing.append(i18n.t(lang, "admin.missing.schedule"))
    return missing


@router.callback_query(AdminCallback.filter(F.action == "publish"))
async def cb_publish(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
    sessions: SessionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await callback.answer()
    excursion = await excursions.get(callback_data.excursion_id)
    if excursion is None:
        await callback.message.answer(i18n.t(lang, "admin.not_found"))
        return
    missing = await _missing_for_publish(
        excursion, lang=lang, i18n=i18n, sessions=sessions
    )
    if missing:
        items = "\n".join(f"• {item}" for item in missing)
        await callback.message.answer(
            i18n.t(lang, "admin.publish_missing", items=items),
            reply_markup=admin_publish_missing_keyboard(
                lang, i18n, excursion_id=excursion.id
            ),
        )
        return
    title = _display_title(excursion, lang, i18n, lang)
    await state.set_state(AdminExcursionForm.confirm_publish)
    await state.update_data(excursion_id=excursion.id, wizard=True)
    await callback.message.answer(
        i18n.t(lang, "admin.publish_confirm", title=title),
        reply_markup=admin_publish_confirm_keyboard(
            lang, i18n, excursion_id=excursion.id
        ),
    )


@router.callback_query(AdminCallback.filter(F.action == "publish_confirm"))
async def cb_publish_confirm(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
    catalog_store: CatalogStore,
    sessions: SessionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await callback.answer()
    excursion = await excursions.get(callback_data.excursion_id)
    if excursion is None:
        await callback.message.answer(i18n.t(lang, "admin.not_found"))
        return
    missing = await _missing_for_publish(
        excursion, lang=lang, i18n=i18n, sessions=sessions
    )
    if missing:
        items = "\n".join(f"• {item}" for item in missing)
        await callback.message.answer(
            i18n.t(lang, "admin.publish_missing", items=items),
            reply_markup=admin_publish_missing_keyboard(
                lang, i18n, excursion_id=excursion.id
            ),
        )
        return
    title = _display_title(excursion, lang, i18n, lang)
    try:
        await excursions.set_status(
            excursion_id=excursion.id,
            expected_version=excursion.version,
            status=ExcursionStatus.PUBLISHED.value,
        )
    except ExcursionRepositoryError as error:
        await _handle_repo_error(
            callback.message,
            lang=lang,
            i18n=i18n,
            error=error,
            excursion_id=excursion.id,
        )
        return
    catalog_store.invalidate()
    await state.clear()
    await callback.message.answer(
        i18n.t(lang, "admin.published_ok", title=title),
        reply_markup=admin_published_done_keyboard(lang, i18n),
    )
    log_event(
        logger,
        action="admin.excursion",
        stage="publish",
        result="ok",
        excursion_id=excursion.id,
    )


@router.callback_query(AdminCallback.filter(F.action == "archive"))
async def cb_archive(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await callback.answer()
    excursion = await excursions.get(callback_data.excursion_id)
    if excursion is None:
        await callback.message.answer(i18n.t(lang, "admin.not_found"))
        return
    title = _display_title(excursion, lang, i18n, lang)
    await state.update_data(excursion_id=excursion.id)
    await callback.message.answer(
        i18n.t(lang, "admin.archive_confirm", title=title),
        reply_markup=admin_archive_confirm_keyboard(
            lang, i18n, excursion_id=excursion.id
        ),
    )


@router.callback_query(AdminCallback.filter(F.action == "archive_confirm"))
async def cb_archive_confirm(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
    catalog_store: CatalogStore,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await state.clear()
    await callback.answer()
    excursion = await excursions.get(callback_data.excursion_id)
    if excursion is None:
        await callback.message.answer(i18n.t(lang, "admin.not_found"))
        return
    if excursion.status == ExcursionStatus.ARCHIVED.value:
        await callback.message.answer(i18n.t(lang, "admin.stale_action"))
        await _send_home(callback.message, lang=lang, i18n=i18n, state=state)
        return
    try:
        await excursions.set_status(
            excursion_id=excursion.id,
            expected_version=excursion.version,
            status=ExcursionStatus.ARCHIVED.value,
        )
    except ExcursionRepositoryError as error:
        await _handle_repo_error(
            callback.message,
            lang=lang,
            i18n=i18n,
            error=error,
            excursion_id=excursion.id,
        )
        return
    catalog_store.invalidate()
    await _send_card(
        callback.message,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        excursion_id=excursion.id,
        notice=i18n.t(lang, "admin.archived"),
    )


@router.callback_query(AdminCallback.filter(F.action == "restore"))
async def cb_restore(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
    catalog_store: CatalogStore,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await state.clear()
    await callback.answer()
    excursion = await excursions.get(callback_data.excursion_id)
    if excursion is None:
        await callback.message.answer(i18n.t(lang, "admin.not_found"))
        return
    try:
        await excursions.set_status(
            excursion_id=excursion.id,
            expected_version=excursion.version,
            status=ExcursionStatus.DRAFT.value,
        )
    except ExcursionRepositoryError as error:
        await _handle_repo_error(
            callback.message,
            lang=lang,
            i18n=i18n,
            error=error,
            excursion_id=excursion.id,
        )
        return
    catalog_store.invalidate()
    await _send_card(
        callback.message,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        excursion_id=excursion.id,
        notice=i18n.t(lang, "admin.restored"),
    )


@router.callback_query(AdminCallback.filter(F.action == "confirm_pending"))
async def cb_confirm_pending(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await state.set_state(AdminExcursionForm.confirm_pending)
    await state.set_data({"excursion_id": callback_data.excursion_id})
    await callback.answer()
    await callback.message.answer(
        i18n.t(lang, "admin.confirm_pending"),
        reply_markup=admin_confirm_pending_keyboard(
            lang, i18n, excursion_id=callback_data.excursion_id
        ),
    )


@router.callback_query(AdminCallback.filter(F.action == "apply_pending"))
async def cb_apply_pending(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
    catalog_store: CatalogStore,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await state.clear()
    await callback.answer()
    excursion = await excursions.get(callback_data.excursion_id)
    if excursion is None:
        await callback.message.answer(i18n.t(lang, "admin.not_found"))
        return
    try:
        await excursions.apply_pending(
            excursion_id=excursion.id,
            expected_version=excursion.version,
        )
    except ExcursionRepositoryError as error:
        await _handle_repo_error(
            callback.message,
            lang=lang,
            i18n=i18n,
            error=error,
            excursion_id=excursion.id,
        )
        return
    catalog_store.invalidate()
    await _send_card(
        callback.message,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        excursion_id=excursion.id,
        notice=i18n.t(lang, "admin.pending_applied"),
    )


@router.callback_query(AdminCallback.filter(F.action == "discard_pending"))
async def cb_discard_pending(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    state: FSMContext,
    langs: LanguageStore,
    i18n: I18n,
    settings: Settings,
    excursions: ExcursionRepository,
    catalog_store: CatalogStore,
) -> None:
    lang = await require_admin(callback, settings, i18n, langs)
    if not lang or not isinstance(callback.message, Message):
        return
    await state.clear()
    await callback.answer()
    excursion = await excursions.get(callback_data.excursion_id)
    if excursion is None:
        await callback.message.answer(i18n.t(lang, "admin.not_found"))
        return
    try:
        await excursions.discard_pending(
            excursion_id=excursion.id,
            expected_version=excursion.version,
        )
    except ExcursionRepositoryError as error:
        await _handle_repo_error(
            callback.message,
            lang=lang,
            i18n=i18n,
            error=error,
            excursion_id=excursion.id,
        )
        return
    catalog_store.invalidate()
    await _send_card(
        callback.message,
        lang=lang,
        i18n=i18n,
        excursions=excursions,
        excursion_id=excursion.id,
        notice=i18n.t(lang, "admin.pending_discarded"),
    )
