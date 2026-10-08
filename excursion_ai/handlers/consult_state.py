from __future__ import annotations

from aiogram.fsm.context import FSMContext

from excursion_ai.forms.states import ConsultForm


async def leave_consult_mode(state: FSMContext) -> None:
    if await state.get_state() == ConsultForm.active.state:
        await state.set_state(None)
