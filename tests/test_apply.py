"""Проверки валидации формы и идемпотентного сохранения заявок."""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta
from decimal import Decimal

from excursion_ai.catalog import load_catalog
from excursion_ai.config import ConfigError, load_settings
from excursion_ai.console import configure_console
from excursion_ai.db.applications import (
    ApplicationCreateData,
    ApplicationOwnershipError,
    ApplicationRepository,
    ApplicationValidationError,
)
from excursion_ai.db.session import dispose_engine, init_engine
from excursion_ai.db.users import UserRepository
from excursion_ai.forms.texts import money, total_amount
from excursion_ai.forms.validation import (
    FormValidationError,
    parse_name,
    parse_participants,
    parse_phone,
    parse_requested_date,
    tomorrow_rome,
)
from excursion_ai.i18n import load_i18n
from excursion_ai.logging_setup import reset_logging_for_tests, setup_logging


def test_validation_rules() -> None:
    today = date(2026, 9, 27)
    tomorrow = tomorrow_rome(today)
    assert parse_requested_date(tomorrow.strftime("%d.%m.%Y"), today=today) == tomorrow
    try:
        parse_requested_date(today.strftime("%d.%m.%Y"), today=today)
        raise AssertionError("today must fail")
    except FormValidationError as error:
        assert error.code == "date_too_early"
    try:
        parse_requested_date("31.02.2027", today=today)
        raise AssertionError("invalid calendar date must fail")
    except FormValidationError as error:
        assert error.code == "date_format"

    assert parse_participants("3") == 3
    for bad in ("0", "21", "x"):
        try:
            parse_participants(bad)
            raise AssertionError(bad)
        except FormValidationError:
            pass

    assert parse_name("Anna-Maria O'Neil") == "Anna-Maria O'Neil"
    assert parse_name("  Иван  ") == "Иван"
    try:
        parse_name("A")
        raise AssertionError("short name")
    except FormValidationError as error:
        assert error.code == "name_length"
    try:
        parse_name("Name123")
        raise AssertionError("digits in name")
    except FormValidationError as error:
        assert error.code == "name_charset"

    assert parse_phone("+391234567890") == "+391234567890"
    assert parse_phone("+39 123 456 7890") == "+391234567890"
    try:
        parse_phone("391234567890")
        raise AssertionError("phone without +")
    except FormValidationError as error:
        assert error.code == "phone_format"

    assert total_amount(money(35), 2) == Decimal("70.00")


async def test_save_keeps_form_on_failure_and_idempotent_after_save() -> None:
    """Сбой сохранения не теряет FSM; повтор после успеха возвращает ту же заявку."""
    from unittest.mock import AsyncMock, MagicMock

    from excursion_ai.handlers.apply_flow import build_create_data, save_application

    data = {
        "submission_key": "key-demo-1",
        "excursion_id": "venice-slow",
        "excursion_title": "Venice Slow",
        "language": "en",
        "requested_date": "2026-10-01",
        "session_id": "22222222-2222-2222-2222-222222222222",
        "session_starts_at": "2026-10-01T10:00:00+02:00",
        "participants_count": 2,
        "customer_name": "Demo Visitor",
        "phone": None,
        "unit_price": "45.00",
        "currency": "EUR",
    }
    state = AsyncMock()
    state.get_data = AsyncMock(return_value=data)
    state.set_data = AsyncMock()

    users = AsyncMock()
    users.get_or_create = AsyncMock()
    apps = AsyncMock()
    apps.create_application = AsyncMock(side_effect=RuntimeError("db down"))

    try:
        await save_application(
            telegram_user_id=1,
            state=state,
            users=users,
            applications=apps,
            lang="en",
        )
        raise AssertionError("expected failure")
    except RuntimeError:
        pass
    assert "saved_application_id" not in data

    saved = MagicMock()
    saved.id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    apps.create_application = AsyncMock(return_value=saved)
    apps.get_for_user = AsyncMock(return_value=saved)

    first = await save_application(
        telegram_user_id=1,
        state=state,
        users=users,
        applications=apps,
        lang="en",
    )
    assert first.id == saved.id
    assert data.get("saved_application_id") == str(saved.id)

    second = await save_application(
        telegram_user_id=1,
        state=state,
        users=users,
        applications=apps,
        lang="en",
    )
    assert second.id == saved.id
    apps.create_application.assert_called_once()
    payload = build_create_data(data)
    assert payload.phone is None
    assert payload.is_demo is True


async def _cleanup(factory, telegram_ids: list[int]) -> None:
    from sqlalchemy import delete

    from excursion_ai.db.models import User

    async with factory() as session:
        async with session.begin():
            await session.execute(delete(User).where(User.telegram_user_id.in_(telegram_ids)))


async def run_db_form_checks() -> None:
    from tests.schedule_helpers import create_test_session, pick_published_excursion_id

    settings = load_settings(require_db=True)
    factory = init_engine(settings)
    users = UserRepository(factory)
    apps = ApplicationRepository(factory)
    excursion_id = await pick_published_excursion_id(factory)
    session_row = await create_test_session(
        factory, excursion_id=excursion_id, capacity=30, days_ahead=25
    )

    tg_a = 9_100_001_001
    tg_b = 9_100_001_002
    await _cleanup(factory, [tg_a, tg_b])
    try:
        await users.get_or_create(tg_a, language="en")
        await users.get_or_create(tg_b, language="it")
        key = f"form-{uuid.uuid4().hex}"
        payload = ApplicationCreateData(
            submission_key=key,
            excursion_id=excursion_id,
            excursion_title="Venice Slow",
            language="en",
            requested_date=session_row.starts_at.date(),
            session_id=session_row.id,
            session_starts_at=session_row.starts_at,
            participants_count=2,
            customer_name="Demo Visitor",
            phone="+391234567890",
            is_demo=True,
            unit_price=money("45"),
            currency="EUR",
        )
        first = await apps.create_application(telegram_user_id=tg_a, data=payload)
        second = await apps.create_application(telegram_user_id=tg_a, data=payload)
        assert first.id == second.id

        no_phone = await apps.create_application(
            telegram_user_id=tg_a,
            data=ApplicationCreateData(
                submission_key=f"form-{uuid.uuid4().hex}",
                excursion_id=excursion_id,
                excursion_title="Venice Slow",
                language="en",
                requested_date=session_row.starts_at.date(),
                session_id=session_row.id,
                session_starts_at=session_row.starts_at,
                participants_count=1,
                customer_name="Demo Visitor",
                phone=None,
                is_demo=True,
                unit_price=money("45"),
                currency="EUR",
            ),
        )
        assert no_phone.phone is None

        try:
            await apps.create_application(
                telegram_user_id=tg_a,
                data=ApplicationCreateData(
                    submission_key=f"bad-{uuid.uuid4().hex}",
                    excursion_id=excursion_id,
                    excursion_title="Venice Slow",
                    language="en",
                    requested_date=session_row.starts_at.date(),
                    session_id=session_row.id,
                    session_starts_at=session_row.starts_at,
                    participants_count=0,
                    customer_name="Demo Visitor",
                    unit_price=money("45"),
                    currency="EUR",
                ),
            )
            raise AssertionError("participants <= 0 must fail")
        except ApplicationValidationError:
            pass

        other_key = f"form-{uuid.uuid4().hex}"
        await apps.create_application(
            telegram_user_id=tg_b,
            data=ApplicationCreateData(
                submission_key=other_key,
                excursion_id=excursion_id,
                excursion_title="Venice Slow",
                language="it",
                requested_date=session_row.starts_at.date(),
                session_id=session_row.id,
                session_starts_at=session_row.starts_at,
                participants_count=1,
                customer_name="Altro Utente",
                unit_price=money("45"),
                currency="EUR",
            ),
        )
        try:
            await apps.create_application(
                telegram_user_id=tg_a,
                data=ApplicationCreateData(
                    submission_key=other_key,
                    excursion_id=excursion_id,
                    excursion_title="Venice Slow",
                    language="en",
                    requested_date=session_row.starts_at.date(),
                    session_id=session_row.id,
                    session_starts_at=session_row.starts_at,
                    participants_count=1,
                    customer_name="Demo Visitor",
                    unit_price=money("45"),
                    currency="EUR",
                ),
            )
            raise AssertionError("foreign submission_key must fail")
        except ApplicationOwnershipError:
            pass

        owned = await apps.get_for_user(application_id=first.id, telegram_user_id=tg_a)
        foreign = await apps.get_for_user(application_id=first.id, telegram_user_id=tg_b)
        assert owned is not None
        assert foreign is None
        assert len(await apps.list_for_user(tg_a)) >= 2
        print("OK apply db checks passed")
    finally:
        await _cleanup(factory, [tg_a, tg_b])
        await dispose_engine()



def main() -> None:
    configure_console()
    setup_logging(force=True)
    load_i18n()
    test_validation_rules()
    print("OK validation")
    asyncio.run(test_save_keeps_form_on_failure_and_idempotent_after_save())
    print("OK save failure / idempotent path")
    try:
        asyncio.run(run_db_form_checks())
    except ConfigError as error:
        print(f"SKIP db apply checks: {error}")
        raise SystemExit(2) from error
    finally:
        reset_logging_for_tests()


if __name__ == "__main__":
    main()
