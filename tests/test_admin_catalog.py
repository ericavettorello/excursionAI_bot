"""Проверки админ-доступа, импорта каталога и версии."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock

from excursion_ai.catalog import load_catalog_from_json
from excursion_ai.config import ConfigError, Settings, load_settings
from excursion_ai.console import configure_console
from excursion_ai.db.excursions import (
    ExcursionRepository,
    ExcursionVersionConflict,
)
from excursion_ai.db.session import dispose_engine, init_engine
from excursion_ai.forms.texts import money
from excursion_ai.handlers.apply_flow import refresh_offer_from_catalog
from excursion_ai.i18n import load_i18n
from excursion_ai.logging_setup import reset_logging_for_tests, setup_logging
from excursion_ai.catalog import Catalog, Excursion


def test_admin_ids_empty_denies() -> None:
    assert Settings(bot_token="x", admin_ids=()).is_admin(1) is False
    assert Settings(bot_token="x", admin_ids=(42,)).is_admin(1) is False
    assert Settings(bot_token="x", admin_ids=(42,)).is_admin(42) is True


def test_seed_json_valid() -> None:
    catalog = load_catalog_from_json()
    assert len(catalog.all()) == 4
    assert catalog.get("padua-intro") is not None


async def run_import_and_repo_checks() -> None:
    settings = load_settings(require_db=True)
    factory = init_engine(settings)
    repo = ExcursionRepository(factory)
    seed = load_catalog_from_json()
    try:
        first = []
        for item in seed.all():
            first.append(await repo.import_seed_item(item))
        second = []
        for item in seed.all():
            second.append(await repo.import_seed_item(item))
        assert all(x in {"inserted", "skipped"} for x in first)
        assert all(x == "skipped" for x in second)
        published = await repo.list_published()
        assert len(published) >= 4
        ids = {item.id for item in published}
        assert "padua-intro" in ids

        # Конфликт версий
        draft_id = "test-admin-draft-conflict"
        if not await repo.exists(draft_id):
            await repo.create_draft(
                excursion_id=draft_id,
                city_id="padua",
                duration_minutes=60,
                price_per_person=Decimal("10.00"),
                currency="EUR",
                conducted_languages=["ru"],
            )
        current = await repo.get(draft_id)
        assert current is not None
        try:
            await repo.update_fields(
                excursion_id=draft_id,
                expected_version=current.version - 1 if current.version > 1 else 999999,
                fields={"duration_minutes": 90},
                apply_immediately=True,
            )
            # If version was 1 and we passed 999999, conflict expected
            raise AssertionError("expected version conflict")
        except ExcursionVersionConflict:
            pass

        # Цена при оформлении
        catalog = Catalog(published)
        state = AsyncMock()
        padua = catalog.get("padua-intro")
        assert padua is not None
        data = {
            "excursion_id": padua.id,
            "excursion_title": padua.title("ru"),
            "unit_price": str(money(padua.price_per_person) + Decimal("5")),
            "currency": padua.currency,
        }
        state.get_data = AsyncMock(return_value=data)
        state.set_data = AsyncMock()
        status = await refresh_offer_from_catalog(state, catalog=catalog, lang="ru")
        assert status == "price_changed"
        assert money(str(data["unit_price"])) == money(padua.price_per_person)

        # Архивная недоступна
        archived_catalog = Catalog(
            [
                Excursion(
                    id="gone",
                    city_id="x",
                    duration_minutes=60,
                    price_per_person=Decimal("1"),
                    currency="EUR",
                    conducted_languages=("ru",),
                    translations={
                        "ru": {
                            "title": "t",
                            "city": "c",
                            "description": "d" * 10,
                            "route": ["a", "b", "c"],
                            "meeting_point": "meet",
                        },
                        "en": {
                            "title": "t",
                            "city": "c",
                            "description": "d" * 10,
                            "route": ["a", "b", "c"],
                            "meeting_point": "meet",
                        },
                        "it": {
                            "title": "t",
                            "city": "c",
                            "description": "d" * 10,
                            "route": ["a", "b", "c"],
                            "meeting_point": "meet",
                        },
                    },
                    status="archived",
                )
            ]
        )
        data2 = {
            "excursion_id": "gone",
            "excursion_title": "t",
            "unit_price": "1.00",
            "currency": "EUR",
        }
        state.get_data = AsyncMock(return_value=data2)
        assert await refresh_offer_from_catalog(state, catalog=archived_catalog, lang="ru") == "unavailable"

        print("OK import idempotent / version / price refresh")
    finally:
        await dispose_engine()


def main() -> None:
    configure_console()
    setup_logging(force=True)
    load_i18n()
    test_admin_ids_empty_denies()
    print("OK admin access gate")
    test_seed_json_valid()
    print("OK seed json")
    try:
        asyncio.run(run_import_and_repo_checks())
    except ConfigError as error:
        print(f"SKIP admin db checks: {error}")
        raise SystemExit(2) from error
    finally:
        reset_logging_for_tests()


if __name__ == "__main__":
    main()
