"""Утилита: показать воронки/этапы amoCRM (без записи секретов в вывод лишнего).

Запуск:
  .\\.venv\\Scripts\\python.exe -m excursion_ai.crm.discover_pipelines
"""

from __future__ import annotations

import asyncio
import sys

from excursion_ai.config import ConfigError, load_settings
from excursion_ai.console import configure_console
from excursion_ai.crm.client import AmoCrmClient


async def _run() -> int:
    settings = load_settings(require_db=False)
    if not settings.amocrm_ready() and not (
        settings.amocrm_subdomain
        and (settings.amocrm_access_token or settings.amocrm_refresh_token)
    ):
        print(
            "Заполните AMOCRM_SUBDOMAIN и токены в .env, затем повторите.\n"
            "Для поиска воронки достаточно access token."
        )
        return 1
    client = AmoCrmClient(settings)
    try:
        pipelines = await client.list_pipelines()
    finally:
        await client.aclose()
    if not pipelines:
        print("Воронки не найдены.")
        return 1
    print("Воронки и этапы (подставьте ID в .env или сверьте имена):\n")
    for pipeline in pipelines:
        print(f"- pipeline_id={pipeline.get('id')}  name={pipeline.get('name')!r}")
        for status in (pipeline.get("_embedded") or {}).get("statuses") or []:
            print(
                f"    status_id={status.get('id')}  name={status.get('name')!r}"
            )
    print(
        "\nПример .env:\n"
        "AMOCRM_PIPELINE_NAME=ЭКСКУРСИИ\n"
        "AMOCRM_STATUS_NAME=Новая заявка\n"
        "# или:\n"
        "AMOCRM_PIPELINE_ID=<id>\n"
        "AMOCRM_STATUS_ID=<id>"
    )
    return 0


def main() -> None:
    configure_console()
    try:
        raise SystemExit(asyncio.run(_run()))
    except ConfigError as error:
        print(error)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
