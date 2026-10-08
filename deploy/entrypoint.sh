#!/bin/sh
set -eu

echo "Waiting for PostgreSQL at ${DB_HOST:-db}:${DB_PORT:-5432}…"
python - <<'PY'
import asyncio
import os
import sys

from excursion_ai.config import load_settings
from excursion_ai.db.session import check_connection, dispose_engine, init_engine


async def wait_db() -> None:
    settings = load_settings(require_db=True)
    delays = [1, 2, 3, 5, 5, 5, 8, 8, 10]
    last_error: Exception | None = None
    for delay in delays:
        try:
            init_engine(settings)
            info = await check_connection(settings)
            print(
                f"PostgreSQL ready: db={info['db_name']} user={info['db_user']}",
                flush=True,
            )
            await dispose_engine()
            return
        except Exception as error:  # noqa: BLE001 — retry loop
            last_error = error
            await dispose_engine()
            await asyncio.sleep(delay)
    print(f"PostgreSQL is not ready: {last_error}", file=sys.stderr)
    raise SystemExit(1)


asyncio.run(wait_db())
PY

echo "Running Alembic migrations…"
python -m alembic upgrade head

echo "Starting ExcursionAI…"
exec python -m excursion_ai
