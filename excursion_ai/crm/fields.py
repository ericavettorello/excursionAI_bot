"""Сопоставление пользовательских полей сделки amoCRM по названию."""

from __future__ import annotations

import logging
from typing import Any, Protocol

from excursion_ai.config import Settings
from excursion_ai.crm.client import AmoCrmApiError
from excursion_ai.log_context import log_event

logger = logging.getLogger(__name__)

PARTICIPANTS_FIELD_NAME = "Участники"
PARTICIPANTS_FIELD_TYPE = "numeric"


class SupportsLeadCustomFields(Protocol):
    async def list_lead_custom_fields(self) -> list[dict[str, Any]]: ...

    async def create_lead_custom_field(
        self,
        *,
        name: str,
        field_type: str = "numeric",
    ) -> dict[str, Any]: ...


def _find_field_id_by_name(fields: list[dict[str, Any]], field_name: str) -> int | None:
    wanted = field_name.strip()
    for item in fields:
        name = str(item.get("name") or "").strip()
        if name == wanted:
            field_id = item.get("id")
            if field_id is None:
                raise AmoCrmApiError("custom_field_missing_id")
            return int(field_id)
    return None


async def resolve_lead_custom_field_id_by_name(
    client: SupportsLeadCustomFields,
    field_name: str,
) -> int:
    """Ищет ID поля сделки по точному названию."""
    wanted = field_name.strip()
    if not wanted:
        raise AmoCrmApiError("custom_field_name_empty")

    fields = await client.list_lead_custom_fields()
    found = _find_field_id_by_name(fields, wanted)
    if found is not None:
        return found
    raise AmoCrmApiError("participants_field_not_found")


async def ensure_participants_field_id(
    client: SupportsLeadCustomFields,
    settings: Settings,
) -> int:
    """Поле «Участники»: найти по имени или создать numeric-поле в CRM."""
    fields = await client.list_lead_custom_fields()
    found = _find_field_id_by_name(fields, PARTICIPANTS_FIELD_NAME)
    if found is not None:
        field_id = found
        created = False
    else:
        log_event(
            logger,
            action="crm.fields",
            stage="participants",
            result="missing_creating",
            field_name=PARTICIPANTS_FIELD_NAME,
        )
        try:
            created_field = await client.create_lead_custom_field(
                name=PARTICIPANTS_FIELD_NAME,
                field_type=PARTICIPANTS_FIELD_TYPE,
            )
            field_id = int(created_field["id"])
            created = True
        except AmoCrmApiError:
            # Гонка: поле могли создать параллельно — перечитываем список.
            fields = await client.list_lead_custom_fields()
            found = _find_field_id_by_name(fields, PARTICIPANTS_FIELD_NAME)
            if found is None:
                log_event(
                    logger,
                    action="crm.fields",
                    stage="participants",
                    result="not_found",
                    level=logging.ERROR,
                    field_name=PARTICIPANTS_FIELD_NAME,
                    available_count=len(fields),
                )
                raise AmoCrmApiError("participants_field_not_found")
            field_id = found
            created = False

    if (
        settings.amocrm_cf_participants is not None
        and int(settings.amocrm_cf_participants) != field_id
    ):
        log_event(
            logger,
            action="crm.fields",
            stage="participants",
            result="env_id_mismatch",
            level=logging.WARNING,
            field_id=field_id,
            env_field_id=settings.amocrm_cf_participants,
        )
    log_event(
        logger,
        action="crm.fields",
        stage="participants",
        result="created" if created else "ok",
        field_id=field_id,
        field_name=PARTICIPANTS_FIELD_NAME,
    )
    return field_id


# Совместимость со старым именем в sync/тестах.
async def resolve_participants_field_id(
    client: SupportsLeadCustomFields,
    settings: Settings,
) -> int:
    return await ensure_participants_field_id(client, settings)
