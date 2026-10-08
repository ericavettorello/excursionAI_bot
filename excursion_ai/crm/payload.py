"""Формирование payload сделки и контакта для amoCRM без сетевых вызовов."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from excursion_ai.config import Settings
from excursion_ai.db.models import Application
from excursion_ai.db.public_number import format_public_number
from excursion_ai.db.sessions import format_rome_date, format_rome_time


def deal_name_for(application: Application) -> str:
    """Публичный номер + экскурсия (+ дата/время сеанса, без UUID)."""
    number = format_public_number(int(application.public_number))
    base = f"ExcursionAI #{number} — {application.excursion_title}"
    if application.session_starts_at is not None:
        day = format_rome_date(application.session_starts_at)
        hhmm = format_rome_time(application.session_starts_at)
        return f"{base} — {day} {hhmm}"
    return base


def deal_budget(application: Application) -> int:
    total = (Decimal(application.unit_price) * application.participants_count).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    return int(total)


def _cf_value(field_id: int, value: str | int) -> dict[str, Any]:
    return {"field_id": field_id, "values": [{"value": value}]}


def build_contact_payload(application: Application) -> dict[str, Any]:
    contact: dict[str, Any] = {"name": application.customer_name.strip()}
    if application.phone:
        contact["custom_fields_values"] = [
            {
                "field_code": "PHONE",
                "values": [{"value": application.phone, "enum_code": "WORK"}],
            }
        ]
    return contact


def build_lead_custom_fields(
    application: Application,
    settings: Settings,
    *,
    participants_field_id: int,
    field_ids: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Кастомные поля сделки. Участники обязательны."""
    if participants_field_id <= 0:
        raise ValueError("participants_field_id_required")
    ids = field_ids or {}

    fields: list[dict[str, Any]] = []
    app_field = ids.get("application_id") or settings.amocrm_cf_application_id
    if app_field:
        fields.append(_cf_value(int(app_field), str(application.id)))

    excursion_field = ids.get("excursion") or settings.amocrm_cf_excursion
    if excursion_field:
        fields.append(_cf_value(int(excursion_field), application.excursion_title))

    public_field = ids.get("public_number") or settings.amocrm_cf_public_number
    if public_field:
        fields.append(
            _cf_value(
                int(public_field),
                format_public_number(int(application.public_number)),
            )
        )

    date_field = ids.get("session_date") or settings.amocrm_cf_session_date or settings.amocrm_cf_requested_date
    if date_field and application.session_starts_at is not None:
        fields.append(
            _cf_value(int(date_field), format_rome_date(application.session_starts_at))
        )
    elif date_field:
        fields.append(
            _cf_value(int(date_field), application.requested_date.isoformat())
        )

    time_field = ids.get("session_time") or settings.amocrm_cf_session_time
    if time_field and application.session_starts_at is not None:
        fields.append(
            _cf_value(int(time_field), format_rome_time(application.session_starts_at))
        )

    fields.append(
        _cf_value(participants_field_id, int(application.participants_count))
    )
    return fields


def build_complex_lead_payload(
    application: Application,
    settings: Settings,
    *,
    pipeline_id: int,
    status_id: int,
    participants_field_id: int,
    field_ids: dict[str, int] | None = None,
) -> dict[str, Any]:
    request_id = str(application.id)
    if isinstance(application.id, UUID):
        request_id = str(application.id)

    lead: dict[str, Any] = {
        "name": deal_name_for(application),
        "price": deal_budget(application),
        "pipeline_id": pipeline_id,
        "status_id": status_id,
        "request_id": request_id,
        "_embedded": {"contacts": [build_contact_payload(application)]},
    }
    lead["custom_fields_values"] = build_lead_custom_fields(
        application,
        settings,
        participants_field_id=participants_field_id,
        field_ids=field_ids,
    )
    return lead


def build_lead_update_payload(
    application: Application,
    settings: Settings,
    *,
    lead_id: int | str,
    participants_field_id: int,
    field_ids: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "id": int(lead_id),
        "name": deal_name_for(application),
        "price": deal_budget(application),
        "custom_fields_values": build_lead_custom_fields(
            application,
            settings,
            participants_field_id=participants_field_id,
            field_ids=field_ids,
        ),
    }
