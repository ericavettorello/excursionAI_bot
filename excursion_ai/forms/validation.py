from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ROME_TZ = ZoneInfo("Europe/Rome")

NAME_RE = re.compile(
    r"^[A-Za-zÀ-ÖØ-öø-ÿА-Яа-яЁёІіЇїЄєҐґ'’\-\s]+$",
)
# E.164-подобная проверка формата, без проверки существования номера.
PHONE_RE = re.compile(r"^\+[1-9]\d{6,14}$")

MIN_PARTICIPANTS = 1
MAX_PARTICIPANTS = 20
MIN_NAME_LEN = 2
MAX_NAME_LEN = 100


class FormValidationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def tomorrow_rome(today: date | None = None) -> date:
    if today is None:
        today = datetime.now(ROME_TZ).date()
    return today + timedelta(days=1)


def now_rome() -> datetime:
    return datetime.now(ROME_TZ)


def format_date_hint(day: date) -> str:
    return day.strftime("%d.%m.%Y")


def parse_requested_date(raw: str, *, today: date | None = None) -> date:
    text = raw.strip()
    try:
        parsed = datetime.strptime(text, "%d.%m.%Y").date()
    except ValueError as error:
        raise FormValidationError("date_format") from error
    # Проверка, что дата реально существует, уже встроена в strptime.
    minimum = tomorrow_rome(today)
    if parsed < minimum:
        raise FormValidationError("date_too_early")
    return parsed


def parse_participants(raw: str) -> int:
    text = raw.strip()
    if not text.isdigit():
        raise FormValidationError("participants_format")
    value = int(text)
    if value < MIN_PARTICIPANTS or value > MAX_PARTICIPANTS:
        raise FormValidationError("participants_range")
    return value


def parse_name(raw: str) -> str:
    value = raw.strip()
    if len(value) < MIN_NAME_LEN or len(value) > MAX_NAME_LEN:
        raise FormValidationError("name_length")
    if not NAME_RE.fullmatch(value):
        raise FormValidationError("name_charset")
    return value


def parse_phone(raw: str) -> str:
    value = re.sub(r"[\s\-()]", "", raw.strip())
    if not PHONE_RE.fullmatch(value):
        raise FormValidationError("phone_format")
    return value
