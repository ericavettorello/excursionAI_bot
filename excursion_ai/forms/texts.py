from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from excursion_ai.db.public_number import format_public_number
from excursion_ai.forms.validation import format_date_hint, tomorrow_rome
from excursion_ai.i18n import I18n


def money(value: Decimal | int | str) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def total_amount(unit_price: Decimal, participants: int) -> Decimal:
    return money(Decimal(unit_price) * participants)


def status_label(lang: str, i18n: I18n, status: str) -> str:
    key = f"status.{status}"
    value = i18n.t(lang, key)
    return value if value != key else status


def format_summary(
    *,
    lang: str,
    i18n: I18n,
    excursion_title: str,
    requested_date: date,
    participants_count: int,
    customer_name: str,
    phone: str | None,
    unit_price: Decimal,
    currency: str,
    session_time: str = "—",
) -> str:
    phone_text = phone if phone else i18n.t(lang, "apply.phone_none")
    total = total_amount(unit_price, participants_count)
    lines = [
        f"<b>{i18n.t(lang, 'apply.summary_title')}</b>",
        "",
        i18n.t(lang, "apply.summary_excursion", title=excursion_title),
        i18n.t(lang, "apply.summary_date", date=format_date_hint(requested_date)),
        i18n.t(lang, "apply.summary_time", time=session_time),
        i18n.t(lang, "apply.summary_participants", count=participants_count),
        i18n.t(lang, "apply.summary_name", name=customer_name),
        i18n.t(lang, "apply.summary_phone", phone=phone_text),
        i18n.t(
            lang,
            "apply.summary_unit_price",
            price=f"{money(unit_price)}",
            currency=currency,
        ),
        i18n.t(
            lang,
            "apply.summary_total",
            total=f"{total}",
            currency=currency,
        ),
    ]
    demo_note = i18n.t(lang, "apply.summary_demo").strip()
    if demo_note:
        lines.append(demo_note)
    return "\n".join(lines)


def format_application_list_item(
    *,
    lang: str,
    i18n: I18n,
    public_number: int,
    excursion_title: str,
    requested_date: date,
    participants_count: int,
    unit_price: Decimal,
    currency: str,
    status: str,
) -> str:
    total = total_amount(unit_price, participants_count)
    number = format_public_number(int(public_number))
    return "\n".join(
        [
            i18n.t(lang, "my.number", number=number),
            f"<b>{excursion_title}</b>",
            i18n.t(lang, "my.date", date=format_date_hint(requested_date)),
            i18n.t(lang, "my.participants", count=participants_count),
            i18n.t(lang, "my.total", total=f"{total}", currency=currency),
            i18n.t(lang, "my.status", status=status_label(lang, i18n, status)),
        ]
    )


def prompt_date(lang: str, i18n: I18n) -> str:
    example = format_date_hint(tomorrow_rome())
    return i18n.t(lang, "apply.ask_date", example=example)


def validation_message(lang: str, i18n: I18n, code: str) -> str:
    return i18n.t(lang, f"apply.error.{code}")


def form_data_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    """Безопасный снимок FSM без персональных полей для логов."""
    return {
        "excursion_id": data.get("excursion_id"),
        "has_date": "requested_date" in data,
        "has_participants": "participants_count" in data,
        "has_name": "customer_name" in data,
        "phone_present": data.get("phone") is not None,
        "phone_skipped": "phone" in data and data.get("phone") is None,
        "has_submission_key": bool(data.get("submission_key")),
        "saved_application_id": data.get("saved_application_id"),
        "return_to_confirm": bool(data.get("return_to_confirm")),
    }
