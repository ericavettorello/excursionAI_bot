from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class ApplyForm(StatesGroup):
    participants = State()
    date = State()
    time = State()
    name = State()
    phone = State()
    confirm = State()
    edit_choice = State()
    conflict = State()


class ConsultForm(StatesGroup):
    active = State()


class AdminScheduleForm(StatesGroup):
    pick_excursion = State()
    list_sessions = State()
    add_date = State()
    add_time = State()
    add_capacity = State()
    edit_capacity = State()
    edit_time = State()
    edit_date = State()
