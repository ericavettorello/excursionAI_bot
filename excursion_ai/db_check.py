"""Проверка подключения к PostgreSQL без вывода секретов."""

from __future__ import annotations

import asyncio
import sys

from excursion_ai.config import ConfigError, load_settings
from excursion_ai.console import configure_console
from excursion_ai.db.session import check_connection, dispose_engine


async def _run() -> int:
    settings = load_settings(require_db=True)
    info = await check_connection(settings)
    print(f"OK database={info['db_name']} user={info['db_user']}")
    await dispose_engine()
    return 0


def main() -> None:
    configure_console()
    try:
        raise SystemExit(asyncio.run(_run()))
    except ConfigError as error:
        print(error)
        raise SystemExit(1) from error
    except Exception as error:
        print(f"Ошибка подключения к базе: {type(error).__name__}")
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
