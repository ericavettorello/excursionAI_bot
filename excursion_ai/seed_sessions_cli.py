"""CLI: заполнить демо-сеансы для опубликованных экскурсий без расписания."""

from __future__ import annotations

import asyncio
import sys

from excursion_ai.config import ConfigError, load_settings
from excursion_ai.console import configure_console
from excursion_ai.db.excursions import ExcursionRepository
from excursion_ai.db.seed_sessions import ensure_demo_schedules
from excursion_ai.db.session import dispose_engine, init_engine
from excursion_ai.db.sessions import SessionRepository
from excursion_ai.logging_setup import setup_logging


async def run() -> int:
    settings = load_settings(require_db=True)
    factory = init_engine(settings)
    try:
        result = await ensure_demo_schedules(
            excursions=ExcursionRepository(factory),
            sessions=SessionRepository(factory),
        )
    finally:
        await dispose_engine()
    print(
        f"Demo schedule: checked={result.excursions_checked}, "
        f"seeded_excursions={result.excursions_seeded}, "
        f"created={result.sessions_created}, skipped_dupes={result.sessions_skipped}"
    )
    return 0


def main() -> None:
    configure_console()
    setup_logging()
    try:
        code = asyncio.run(run())
    except ConfigError as error:
        print(error)
        code = 1
    except Exception as error:
        print(f"Error: {type(error).__name__}: {error}")
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
