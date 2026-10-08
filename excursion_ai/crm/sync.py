"""Синхронизация сохранённых заявок с amoCRM после commit в PostgreSQL."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from excursion_ai.config import Settings
from excursion_ai.crm.client import (
    AmoCrmApiError,
    AmoCrmAuthError,
    AmoCrmClient,
    AmoCrmUncertainError,
)
from excursion_ai.crm.fields import resolve_participants_field_id
from excursion_ai.crm.payload import (
    build_complex_lead_payload,
    build_lead_update_payload,
    deal_name_for,
)
from excursion_ai.crm.pipelines import resolve_pipeline_target
from excursion_ai.db.applications import ApplicationRepository
from excursion_ai.db.models import Application, CrmSyncStatus
from excursion_ai.db.public_number import format_public_number
from excursion_ai.db.sessions import format_rome_date, format_rome_time
from excursion_ai.log_context import log_event

logger = logging.getLogger(__name__)


class ApplicationCrmSyncService:
    def __init__(
        self,
        settings: Settings,
        applications: ApplicationRepository,
        *,
        client: AmoCrmClient | None = None,
    ) -> None:
        self._settings = settings
        self._applications = applications
        self._client = client
        self._owns_client = client is None
        self._lock = asyncio.Lock()

    def is_available(self) -> bool:
        return self._settings.amocrm_ready()

    def missing_settings(self) -> list[str]:
        return self._settings.amocrm_missing_settings()

    def _get_client(self) -> AmoCrmClient:
        if self._client is None:
            self._client = AmoCrmClient(self._settings)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    def schedule_sync(self, application_id: uuid.UUID) -> None:
        """Фоновая отправка: не должна ломать ответ пользователю."""
        if not self.is_available():
            return

        async def _runner() -> None:
            try:
                await self.sync_application(application_id)
            except Exception as error:
                log_event(
                    logger,
                    action="crm.sync",
                    stage="background",
                    result="error",
                    level=logging.ERROR,
                    application_id=str(application_id),
                    error_type=type(error).__name__,
                )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(_runner())

    async def sync_application(self, application_id: uuid.UUID) -> Application | None:
        async with self._lock:
            return await self._sync_locked(application_id)

    async def _sync_locked(self, application_id: uuid.UUID) -> Application | None:
        application = await self._applications.get_by_id(application_id)
        if application is None:
            log_event(
                logger,
                action="crm.sync",
                stage="load",
                result="not_found",
                application_id=str(application_id),
            )
            return None

        if not self.is_available():
            if application.crm_sync_status != CrmSyncStatus.NOT_CONNECTED.value:
                await self._applications.mark_crm_status(
                    application_id=application_id,
                    status=CrmSyncStatus.NOT_CONNECTED.value,
                    clear_error=True,
                )
            log_event(
                logger,
                action="crm.sync",
                stage="skip",
                result="not_connected",
                application_id=str(application_id),
            )
            return await self._applications.get_by_id(application_id)

        if application.crm_lead_id and application.crm_sync_status == CrmSyncStatus.SENT.value:
            log_event(
                logger,
                action="crm.sync",
                stage="skip",
                result="already_sent",
                application_id=str(application_id),
            )
            return application

        if int(application.crm_sync_attempts) >= self._settings.amocrm_max_attempts:
            if application.crm_sync_status != CrmSyncStatus.NEEDS_REVIEW.value:
                await self._applications.mark_crm_status(
                    application_id=application_id,
                    status=CrmSyncStatus.NEEDS_REVIEW.value,
                    error_code="max_attempts",
                )
            log_event(
                logger,
                action="crm.sync",
                stage="skip",
                result="max_attempts",
                application_id=str(application_id),
            )
            return await self._applications.get_by_id(application_id)

        await self._applications.mark_crm_status(
            application_id=application_id,
            status=CrmSyncStatus.PENDING.value,
            increment_attempts=True,
            clear_error=True,
        )
        application = await self._applications.get_by_id(application_id)
        assert application is not None

        client = self._get_client()
        log_event(
            logger,
            action="crm.sync",
            stage="start",
            result="pending",
            application_id=str(application_id),
            public_number=application.public_number,
            attempts=application.crm_sync_attempts,
        )

        try:
            participants_field_id = await resolve_participants_field_id(
                client, self._settings
            )
            field_ids = await self._resolve_optional_fields(client)
            existing = await self._find_existing_lead(client, application)
            if existing is not None:
                lead_id = str(existing.get("id"))
                contact_id = _contact_id_from_lead(existing)
                await client.update_lead(
                    build_lead_update_payload(
                        application,
                        self._settings,
                        lead_id=lead_id,
                        participants_field_id=participants_field_id,
                        field_ids=field_ids,
                    )
                )
                updated = await self._applications.mark_crm_status(
                    application_id=application_id,
                    status=CrmSyncStatus.SENT.value,
                    lead_id=lead_id,
                    contact_id=contact_id,
                    clear_error=True,
                )
                log_event(
                    logger,
                    action="crm.sync",
                    stage="reconcile",
                    result="sent",
                    application_id=str(application_id),
                    lead_id=lead_id,
                    participants=application.participants_count,
                )
                return updated

            target = await resolve_pipeline_target(client, self._settings)
            log_event(
                logger,
                action="crm.sync",
                stage="pipeline",
                result="ok",
                application_id=str(application_id),
                pipeline_id=target.pipeline_id,
                status_id=target.status_id,
            )
            payload = build_complex_lead_payload(
                application,
                self._settings,
                pipeline_id=target.pipeline_id,
                status_id=target.status_id,
                participants_field_id=participants_field_id,
                field_ids=field_ids,
            )
            lead_id, contact_id = await client.create_lead_complex(payload)
            updated = await self._applications.mark_crm_status(
                application_id=application_id,
                status=CrmSyncStatus.SENT.value,
                lead_id=lead_id,
                contact_id=contact_id,
                clear_error=True,
            )
            log_event(
                logger,
                action="crm.sync",
                stage="create",
                result="sent",
                application_id=str(application_id),
                lead_id=lead_id,
                participants=application.participants_count,
            )
            return updated
        except AmoCrmUncertainError as error:
            # Перед needs_review пробуем сверку; если нельзя — ручная проверка.
            try:
                existing = await self._find_existing_lead(client, application)
            except Exception:
                existing = None
            if existing is not None:
                try:
                    participants_field_id = await resolve_participants_field_id(
                        client, self._settings
                    )
                    await client.update_lead(
                        build_lead_update_payload(
                            application,
                            self._settings,
                            lead_id=str(existing.get("id")),
                            participants_field_id=participants_field_id,
                        )
                    )
                except Exception as update_error:
                    updated = await self._applications.mark_crm_status(
                        application_id=application_id,
                        status=CrmSyncStatus.NEEDS_REVIEW.value,
                        error_code=getattr(
                            update_error, "code", type(update_error).__name__
                        )[:64],
                    )
                    log_event(
                        logger,
                        action="crm.sync",
                        stage="uncertain_reconcile",
                        result="needs_review",
                        application_id=str(application_id),
                        error_code=updated.crm_last_error_code if updated else None,
                    )
                    return updated
                updated = await self._applications.mark_crm_status(
                    application_id=application_id,
                    status=CrmSyncStatus.SENT.value,
                    lead_id=str(existing.get("id")),
                    contact_id=_contact_id_from_lead(existing),
                    clear_error=True,
                )
                log_event(
                    logger,
                    action="crm.sync",
                    stage="uncertain_reconcile",
                    result="sent",
                    application_id=str(application_id),
                )
                return updated
            updated = await self._applications.mark_crm_status(
                application_id=application_id,
                status=CrmSyncStatus.NEEDS_REVIEW.value,
                error_code=error.code,
            )
            log_event(
                logger,
                action="crm.sync",
                stage="uncertain",
                result="needs_review",
                application_id=str(application_id),
                error_code=error.code,
            )
            return updated
        except (AmoCrmAuthError, AmoCrmApiError) as error:
            updated = await self._applications.mark_crm_status(
                application_id=application_id,
                status=CrmSyncStatus.ERROR.value,
                error_code=error.code,
            )
            log_event(
                logger,
                action="crm.sync",
                stage="failed",
                result="error",
                level=logging.ERROR,
                application_id=str(application_id),
                error_code=error.code,
            )
            return updated
        except Exception as error:
            updated = await self._applications.mark_crm_status(
                application_id=application_id,
                status=CrmSyncStatus.ERROR.value,
                error_code=type(error).__name__[:64],
            )
            log_event(
                logger,
                action="crm.sync",
                stage="failed",
                result="error",
                level=logging.ERROR,
                application_id=str(application_id),
                error_type=type(error).__name__,
            )
            return updated

    async def _resolve_optional_fields(self, client: AmoCrmClient) -> dict[str, int]:
        """Сопоставляет доп. поля по имени; отсутствующие не теряем молча — пишем в лог."""
        wanted = {
            "excursion": "Экскурсия",
            "public_number": "Публичный номер",
            "session_date": "Дата сеанса",
            "session_time": "Время начала",
        }
        resolved: dict[str, int] = {}
        try:
            fields = await client.list_lead_custom_fields()
        except Exception:
            log_event(
                logger,
                action="crm.fields",
                stage="optional",
                result="list_failed",
                level=logging.ERROR,
            )
            return resolved
        by_name = {
            str(item.get("name") or "").strip(): int(item["id"])
            for item in fields
            if item.get("id") is not None
        }
        missing: list[str] = []
        for key, name in wanted.items():
            if name in by_name:
                resolved[key] = by_name[name]
            else:
                missing.append(name)
        if missing:
            log_event(
                logger,
                action="crm.fields",
                stage="optional",
                result="missing",
                level=logging.WARNING,
                missing=",".join(missing),
                hint=(
                    "Создайте в amoCRM поля сделки с точными именами: "
                    + ", ".join(missing)
                ),
            )
        return resolved

    async def _find_existing_lead(
        self,
        client: AmoCrmClient,
        application: Application,
    ) -> dict[str, Any] | None:
        if application.crm_lead_id:
            found = await client.get_lead(application.crm_lead_id)
            if found is not None:
                return found

        if self._settings.amocrm_cf_application_id:
            found = await client.find_lead_by_application_field(
                field_id=self._settings.amocrm_cf_application_id,
                application_id=str(application.id),
            )
            if found is not None:
                return found

        public = format_public_number(int(application.public_number))
        queries = (
            f"ExcursionAI #{public}",
            f"ExcursionAI #{application.id}",
        )
        expected_name = deal_name_for(application)
        for query in queries:
            found = await client.find_lead_by_query(query)
            if found is None:
                continue
            name = str(found.get("name") or "")
            if (
                expected_name == name
                or f"#{public}" in name
                or str(application.id) in name
            ):
                return found
        return None

    async def retry_pending(self, *, limit: int = 10) -> int:
        if not self.is_available():
            return 0
        candidates = await self._applications.list_crm_retry_candidates(limit=limit)
        synced = 0
        for item in candidates:
            result = await self.sync_application(item.id)
            if result and result.crm_sync_status == CrmSyncStatus.SENT.value:
                synced += 1
        return synced


def _contact_id_from_lead(lead: dict[str, Any]) -> str | None:
    embedded = lead.get("_embedded") or {}
    contacts = embedded.get("contacts") or []
    if contacts and contacts[0].get("id") is not None:
        return str(contacts[0]["id"])
    return None


def build_crm_sync_service(
    settings: Settings,
    applications: ApplicationRepository,
    *,
    client: AmoCrmClient | None = None,
) -> ApplicationCrmSyncService:
    return ApplicationCrmSyncService(settings, applications, client=client)
