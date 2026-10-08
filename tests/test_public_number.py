"""Публичный номер заявки: формат, UNIQUE и sequence в PostgreSQL."""

from __future__ import annotations

import asyncio
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import text

from excursion_ai.config import load_settings
from excursion_ai.db.applications import ApplicationCreateData, ApplicationRepository
from excursion_ai.db.models import CrmSyncStatus
from excursion_ai.db.public_number import format_public_number
from excursion_ai.db.session import dispose_engine, init_engine
from excursion_ai.db.users import UserRepository


def test_format_public_number() -> None:
    assert format_public_number(1) == "0001"
    assert format_public_number(12) == "0012"
    assert format_public_number(9999) == "9999"
    assert format_public_number(10000) == "10000"
    try:
        format_public_number(0)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


async def _test_public_number_sequence_and_parallel() -> None:
    from tests.schedule_helpers import create_test_session, pick_published_excursion_id
    settings = load_settings(require_db=True)
    factory = init_engine(settings)
    users = UserRepository(factory)
    apps = ApplicationRepository(factory)
    tg_base = 9_100_000_000 + (uuid.uuid4().int % 1_000_000)

    try:
        async with factory() as session:
            exists = await session.scalar(
                text(
                    """
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'applications'
                      AND column_name = 'public_number'
                    """
                )
            )
            if exists is None:
                raise AssertionError("public_number column missing — apply migration 20261001_0006")

            uniq = await session.scalar(
                text(
                    """
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'uq_applications_public_number'
                    """
                )
            )
            assert uniq is not None

            # Параллельные nextval без гонки на одно значение.
            values = (
                await session.execute(
                    text(
                        """
                        SELECT nextval('applications_public_number_seq')
                        FROM generate_series(1, 20)
                        """
                    )
                )
            ).scalars().all()
            assert len(values) == len(set(values))
            assert all(int(v) > 0 for v in values)

        await users.get_or_create(telegram_user_id=tg_base, language="ru")
        excursion_id = await pick_published_excursion_id(factory)
        session_row = await create_test_session(
            factory, excursion_id=excursion_id, capacity=50, days_ahead=40
        )

        async def _create(index: int):
            return await apps.create_application(
                telegram_user_id=tg_base,
                data=ApplicationCreateData(
                    submission_key=f"pn-{tg_base}-{index}-{uuid.uuid4().hex[:8]}",
                    excursion_id=excursion_id,
                    excursion_title="Venice without the rush",
                    language="ru",
                    requested_date=session_row.starts_at.date(),
                    session_id=session_row.id,
                    session_starts_at=session_row.starts_at,
                    participants_count=2,
                    customer_name="Parallel Tester",
                    unit_price=Decimal("10.00"),
                    currency="EUR",
                    phone=None,
                    is_demo=True,
                ),
                crm_sync_status=CrmSyncStatus.NOT_CONNECTED.value,
            )

        created = await asyncio.gather(*[_create(i) for i in range(5)])
        numbers = [int(item.public_number) for item in created]
        assert len(numbers) == len(set(numbers))
        assert all(n > 0 for n in numbers)
        for item in created:
            assert format_public_number(int(item.public_number)) == (
                f"{int(item.public_number):04d}"
            )
    finally:
        await dispose_engine()


if __name__ == "__main__":
    test_format_public_number()
    print("OK format_public_number")
    asyncio.run(_test_public_number_sequence_and_parallel())
    print("OK public_number sequence + parallel create")
    print("OK public_number suite")
