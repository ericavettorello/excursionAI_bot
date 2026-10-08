from __future__ import annotations

import logging
import re
import secrets
from contextvars import ContextVar
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Короткий идентификатор обработки входящего события (не Telegram ID).
event_id_var: ContextVar[str] = ContextVar("event_id", default="-")

_TELEGRAM_TOKEN_RE = re.compile(r"\d{6,}:[A-Za-z0-9_-]{20,}")
_BEARER_RE = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-+=/]+")
_ENV_ASSIGN_RE = re.compile(
    r"(?i)\b(bot_token|api_key|token|password|secret|authorization|db_password)\s*[=:]\s*([^\s,;]+)"
)
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:access_token|api_key|token|key|password|secret)=)([^&\s]+)"
)
_DB_URL_RE = re.compile(
    r"(?i)\b(postgres(?:ql)?(?:\+[\w]+)?://)([^:@/\s]+):([^@/\s]+)@"
)

_extra_secret_patterns: list[re.Pattern[str]] = []


def new_event_id() -> str:
    return secrets.token_hex(4)


def get_event_id() -> str:
    return event_id_var.get()


def set_event_id(event_id: str):
    return event_id_var.set(event_id)


def reset_event_id(token) -> None:
    event_id_var.reset(token)


def mark_exception_logged(exc: BaseException) -> None:
    setattr(exc, "_excursion_ai_logged", True)


def is_exception_logged(exc: BaseException) -> bool:
    return bool(getattr(exc, "_excursion_ai_logged", False))


def register_secret_value(value: str | None) -> None:
    """Регистрирует известный секрет для маскирования в журналах."""
    if not value or len(value) < 8:
        return
    escaped = re.escape(value)
    pattern = re.compile(escaped)
    if pattern not in _extra_secret_patterns:
        _extra_secret_patterns.append(pattern)


def redact_secrets(text: str) -> str:
    if not text:
        return text
    redacted = _TELEGRAM_TOKEN_RE.sub("[REDACTED_TOKEN]", text)
    redacted = _DB_URL_RE.sub(r"\1\2:[REDACTED]@", redacted)
    redacted = _BEARER_RE.sub(r"\1[REDACTED]", redacted)
    redacted = _ENV_ASSIGN_RE.sub(r"\1=[REDACTED]", redacted)
    redacted = _QUERY_SECRET_RE.sub(r"\1[REDACTED]", redacted)
    for pattern in _extra_secret_patterns:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def local_timezone():
    try:
        return datetime.now().astimezone().tzinfo or ZoneInfo("UTC")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


class ContextInjectFilter(logging.Filter):
    """Добавляет event_id/action/stage в LogRecord из контекста и extra."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "event_id") or not record.event_id:
            record.event_id = get_event_id() or "-"
        if not hasattr(record, "action") or record.action in (None, ""):
            record.action = "-"
        if not hasattr(record, "stage") or record.stage in (None, ""):
            record.stage = "-"
        return True


class SecretRedactFilter(logging.Filter):
    """Маскирует токены и типичные секреты в сообщениях и traceback."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_secrets(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    key: redact_secrets(value) if isinstance(value, str) else value
                    for key, value in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    redact_secrets(arg) if isinstance(arg, str) else arg
                    for arg in record.args
                )
        if record.exc_text:
            record.exc_text = redact_secrets(record.exc_text)
        return True


class EventFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__(
            fmt=(
                "%(asctime)s %(levelname)s %(name)s "
                "event_id=%(event_id)s action=%(action)s stage=%(stage)s %(message)s"
            )
        )
        self.converter = lambda timestamp: datetime.fromtimestamp(
            timestamp, tz=local_timezone()
        ).timetuple()

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created, tz=local_timezone())
        return dt.isoformat(timespec="seconds")

    def formatException(self, ei) -> str:  # noqa: ANN001
        # Стандартный traceback без локальных переменных.
        text = super().formatException(ei)
        return redact_secrets(text)

    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets(super().format(record))


def log_event(
    logger: logging.Logger,
    *,
    action: str,
    stage: str,
    result: str,
    level: int = logging.INFO,
    exc_info: Any = None,
    **fields: object,
) -> None:
    parts = [f"result={result}"]
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
    logger.log(
        level,
        " ".join(parts),
        extra={"action": action, "stage": stage},
        exc_info=exc_info,
    )


def log_exception(
    logger: logging.Logger,
    *,
    action: str,
    stage: str,
    exc: BaseException,
    result: str = "error",
    **fields: object,
) -> None:
    if is_exception_logged(exc):
        return
    mark_exception_logged(exc)
    fields = {"error_type": type(exc).__name__, **fields}
    log_event(
        logger,
        action=action,
        stage=stage,
        result=result,
        level=logging.ERROR,
        exc_info=exc,
        **fields,
    )
