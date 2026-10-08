from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

from excursion_ai.config import PROJECT_ROOT
from excursion_ai.log_context import (
    ContextInjectFilter,
    EventFormatter,
    SecretRedactFilter,
    register_secret_value,
)

LOGS_DIR = PROJECT_ROOT / "logs"
DEFAULT_LOG_FILE = LOGS_DIR / "app.log"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5

_CONFIGURED = False


def _parse_level(value: str | None) -> int:
    name = (value or "INFO").strip().upper()
    level = logging.getLevelName(name)
    if isinstance(level, int):
        return level
    return logging.INFO


def reset_logging_for_tests() -> None:
    """Сбрасывает флаг настройки (только для тестов)."""
    global _CONFIGURED
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    _CONFIGURED = False


def setup_logging(
    *,
    log_level: str | None = None,
    log_file: Path | None = None,
    max_bytes: int = MAX_BYTES,
    backup_count: int = BACKUP_COUNT,
    force: bool = False,
) -> Path:
    """Единая настройка логирования. Повторный вызов без force игнорируется."""
    global _CONFIGURED
    if _CONFIGURED and not force:
        return log_file or DEFAULT_LOG_FILE

    load_dotenv(PROJECT_ROOT / ".env")
    level = _parse_level(log_level or os.getenv("LOG_LEVEL", "INFO"))
    target = log_file or DEFAULT_LOG_FILE
    target.parent.mkdir(parents=True, exist_ok=True)

    token = os.getenv("BOT_TOKEN", "").strip()
    register_secret_value(token)

    formatter = EventFormatter()
    context_filter = ContextInjectFilter()
    secret_filter = SecretRedactFilter()

    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(formatter)
    console.addFilter(context_filter)
    console.addFilter(secret_filter)

    file_handler = RotatingFileHandler(
        target,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(context_filter)
    file_handler.addFilter(secret_filter)

    root = logging.getLogger()
    if force:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()

    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(console)
    root.addHandler(file_handler)

    # Сторонние библиотеки: без шума и без риска утечки в DEBUG.
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    _CONFIGURED = True
    return target
