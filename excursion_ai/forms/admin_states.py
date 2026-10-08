from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class AdminExcursionForm(StatesGroup):
    """Мастер создания и точечного редактирования экскурсий."""

    city = State()
    duration = State()
    price = State()
    languages = State()
    source_lang = State()
    title = State()
    description = State()
    route = State()
    meeting = State()
    translate_offer = State()
    translate_review = State()
    photo = State()
    photo_manage = State()
    photo_replace = State()
    lang_hub = State()
    preview = State()
    edit_field = State()
    edit_confirm = State()
    confirm_pending = State()
    confirm_publish = State()
    confirm_retranslate = State()
