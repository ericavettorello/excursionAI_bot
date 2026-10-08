"""Интеграционные проверки PostgreSQL с откатом тестовых данных."""

from __future__ import annotations

import asyncio
import uuid
from datetime import date
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
from excursion_ai.languages import LanguageStore
from excursion_ai.log_context import redact_secrets
from excursion_ai.logging_setup import reset_logging_for_tests, setup_logging


async def _cleanup_users(session_factory, telegram_ids: list[int]) -> None:
    from sqlalchemy import delete

    from excursion_ai.db.models import User

    async with session_factory() as session:
        async with session.begin():
            await session.execute(delete(User).where(User.telegram_user_id.in_(telegram_ids)))


async def run_db_checks() -> None:
    from tests.schedule_helpers import create_test_session, pick_published_excursion_id

    settings = load_settings(require_db=True)
    factory = init_engine(settings)
    users = UserRepository(factory)
    apps = ApplicationRepository(factory)
    langs = LanguageStore(users)
    catalog = load_catalog()
    excursion = catalog.get("padua-intro")
    assert excursion is not None

    tg_a = 9_000_001_001
    tg_b = 9_000_001_002
    await _cleanup_users(factory, [tg_a, tg_b])

    try:
        first = await users.get_or_create(tg_a)
        second = await users.get_or_create(tg_a)
        assert first.id == second.id

        await langs.set(tg_a, "en")
        assert await langs.get(tg_a) == "en"
        # имитация перезапуска кэша
        langs_reloaded = LanguageStore(users)
        assert await langs_reloaded.get(tg_a) == "en"

        await users.get_or_create(tg_b, language="it")
        excursion_id = await pick_published_excursion_id(factory)
        session_row = await create_test_session(
            factory, excursion_id=excursion_id, capacity=30, days_ahead=30
        )
        submission = f"demo-{uuid.uuid4().hex}"
        payload = ApplicationCreateData(
            submission_key=submission,
            excursion_id=excursion_id,
            excursion_title=excursion.title("en"),
            language="en",
            requested_date=session_row.starts_at.date(),
            session_id=session_row.id,
            session_starts_at=session_row.starts_at,
            participants_count=2,
            customer_name="Demo Visitor",
            phone=None,
            is_demo=True,
            unit_price=Decimal(excursion.price_per_person),
            currency=excursion.currency,
        )
        created = await apps.create_application(telegram_user_id=tg_a, data=payload)
        again = await apps.create_application(telegram_user_id=tg_a, data=payload)
        assert created.id == again.id

        owned = await apps.get_for_user(application_id=created.id, telegram_user_id=tg_a)
        assert owned is not None
        foreign = await apps.get_for_user(application_id=created.id, telegram_user_id=tg_b)
        assert foreign is None

        listed = await apps.list_for_user(tg_a)
        assert any(item.id == created.id for item in listed)
        assert all(True for _ in await apps.list_for_user(tg_b) for __ in []) or True
        assert await apps.list_for_user(tg_b) == []

        try:
            await apps.create_application(
                telegram_user_id=tg_a,
                data=ApplicationCreateData(
                    submission_key=f"bad-{uuid.uuid4().hex}",
                    excursion_id=excursion_id,
                    excursion_title=excursion.title("en"),
                    language="en",
                    requested_date=session_row.starts_at.date(),
                    session_id=session_row.id,
                    session_starts_at=session_row.starts_at,
                    participants_count=0,
                    customer_name="Demo Visitor",
                    unit_price=Decimal(excursion.price_per_person),
                    currency=excursion.currency,
                ),
            )
            raise AssertionError("expected participants validation error")
        except ApplicationValidationError:
            pass

        # Чужой submission_key: создаём заявку B, затем A пытается тем же ключом.
        other_key = f"other-{uuid.uuid4().hex}"
        await apps.create_application(
            telegram_user_id=tg_b,
            data=ApplicationCreateData(
                submission_key=other_key,
                excursion_id=excursion_id,
                excursion_title=excursion.title("it"),
                language="it",
                requested_date=session_row.starts_at.date(),
                session_id=session_row.id,
                session_starts_at=session_row.starts_at,
                participants_count=1,
                customer_name="Altro Utente",
                unit_price=Decimal(excursion.price_per_person),
                currency=excursion.currency,
            ),
        )
        try:
            await apps.create_application(
                telegram_user_id=tg_a,
                data=ApplicationCreateData(
                    submission_key=other_key,
                    excursion_id=excursion_id,
                    excursion_title=excursion.title("en"),
                    language="en",
                    requested_date=session_row.starts_at.date(),
                    session_id=session_row.id,
                    session_starts_at=session_row.starts_at,
                    participants_count=1,
                    customer_name="Demo Visitor",
                    unit_price=Decimal(excursion.price_per_person),
                    currency=excursion.currency,
                ),
            )
            raise AssertionError("expected ownership error")
        except ApplicationOwnershipError:
            pass

        # Проверка отсутствия секретов в строках ошибок подключения.
        fake = "postgresql+asyncpg://excursion_ai_app:SuperSecretPass123@127.0.0.1:5432/excursion_ai"
        redacted = redact_secrets(fake)
        assert "SuperSecretPass123" not in redacted
        assert "[REDACTED]" in redacted

        print("OK db checks passed")
    finally:
        await _cleanup_users(factory, [tg_a, tg_b])
        await dispose_engine()


def main() -> None:
    configure_console()
    setup_logging(force=True)
    try:
        asyncio.run(run_db_checks())
    except ConfigError as error:
        print(f"SKIP db checks: {error}")
        raise SystemExit(2) from error
    except Exception as error:
        print(f"FAIL db checks: {type(error).__name__}")
        raise SystemExit(1) from error
    finally:
        reset_logging_for_tests()


if __name__ == "__main__":
    main()
