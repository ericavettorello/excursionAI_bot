"""Импорт data/excursions.json в PostgreSQL (без автозапуска при старте бота)."""

from __future__ import annotations

import asyncio
import sys

from excursion_ai.catalog import CatalogError, load_catalog_from_json
from excursion_ai.config import ConfigError, load_settings
from excursion_ai.console import configure_console
from excursion_ai.db.excursions import ExcursionRepository
from excursion_ai.db.session import dispose_engine, init_engine
from excursion_ai.logging_setup import setup_logging


async def run_import() -> int:
    settings = load_settings(require_db=True)
    try:
        catalog = load_catalog_from_json()
    except CatalogError as error:
        print(f"Проверка JSON не пройдена: {error}")
        return 1

    factory = init_engine(settings)
    repo = ExcursionRepository(factory)
    inserted = 0
    skipped = 0
    try:
        for item in catalog.all():
            result = await repo.import_seed_item(item)
            if result == "inserted":
                inserted += 1
                print(f"INSERT {item.id}")
            else:
                skipped += 1
                print(f"SKIP  {item.id} (уже есть — не перезаписываем)")
    finally:
        await dispose_engine()

    print(
        f"Импорт завершён: добавлено {inserted}, пропущено {skipped}, "
        f"всего в файле {len(catalog.all())}."
    )
    if inserted == 0 and skipped == len(catalog.all()):
        print("Повторный запуск не создал дубликатов и не изменил данные оператора.")
    return 0


def main() -> None:
    configure_console()
    setup_logging()
    try:
        code = asyncio.run(run_import())
    except ConfigError as error:
        print(error)
        code = 1
    except Exception as error:
        print(f"Ошибка импорта: {type(error).__name__}")
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
