"""Обработка входящих смен этапа сделки amoCRM (бронь)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from excursion_ai.config import Settings
from excursion_ai.crm.client import AmoCrmApiError, AmoCrmClient
from excursion_ai.crm.pipelines import resolve_booking_status_ids
from excursion_ai.db.booking import BookingResult, BookingService
from excursion_ai.db.models import Application, CrmWebhookEvent
from excursion_ai.log_context import log_event

logger = logging.getLogger(__name__)

PIPELINE_NAME_DEFAULT = "ЭКСКУРСИИ"
STATUS_CONFIRMED_NAME = "Бронь подтверждена"
STATUS_CANCELLED_NAME = "Бронь отменена"


class BookingInboundService:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        booking: BookingService,
        *,
        client: AmoCrmClient | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._booking = booking
        self._client = client
        self._owns_client = client is None
        self._status_cache: dict[str, int] | None = None

    def _get_client(self) -> AmoCrmClient:
        if self._client is None:
            self._client = AmoCrmClient(self._settings)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def _booking_status_ids(self) -> dict[str, int]:
        if self._status_cache is not None:
            return self._status_cache
        client = self._get_client()
        ids = await resolve_booking_status_ids(client, self._settings)
        self._status_cache = ids
        return ids

    async def process_lead_status_change(
        self,
        *,
        lead_id: str,
        status_id: int | None,
        event_key: str,
    ) -> BookingResult | None:
        if await self._event_seen(event_key):
            log_event(
                logger,
                action="crm.webhook",
                stage="idempotent",
                result="skip",
                lead_id=str(lead_id),
            )
            return None

        client = self._get_client()
        lead = await client.get_lead(lead_id)
        if lead is None:
            await self._store_event(event_key, lead_id, status_id, "lead_not_found")
            log_event(
                logger,
                action="crm.webhook",
                stage="lead",
                result="not_found",
                level=logging.ERROR,
                lead_id=str(lead_id),
            )
            return None

        actual_status_id = int(lead.get("status_id") or 0)
        try:
            status_map = await self._booking_status_ids()
        except AmoCrmApiError as error:
            await self._store_event(event_key, lead_id, actual_status_id, error.code)
            log_event(
                logger,
                action="crm.webhook",
                stage="status_resolve",
                result="error",
                level=logging.ERROR,
                error_code=error.code,
            )
            return None

        confirmed_id = status_map["confirmed"]
        cancelled_id = status_map["cancelled"]
        if actual_status_id not in {confirmed_id, cancelled_id}:
            await self._store_event(event_key, lead_id, actual_status_id, "ignored_status")
            return None

        application = await self._booking.get_by_crm_lead_id(str(lead_id))
        if application is None:
            await self._store_event(event_key, lead_id, actual_status_id, "app_not_found")
            log_event(
                logger,
                action="crm.webhook",
                stage="application",
                result="not_found",
                level=logging.ERROR,
                lead_id=str(lead_id),
            )
            return None

        if actual_status_id == confirmed_id:
            result = await self._booking.confirm_booking(application.id)
            await self._store_event(
                event_key,
                lead_id,
                actual_status_id,
                "conflict" if result.conflict else result.code,
            )
            if result.conflict:
                await self._add_conflict_note(client, lead_id, result.code)
            return result

        result = await self._booking.cancel_booking(application.id)
        await self._store_event(event_key, lead_id, actual_status_id, result.code)
        return result

    async def reconcile_open_leads(self, *, limit: int = 30) -> int:
        """Периодическая сверка: заявки new/confirmed с lead_id."""
        if not self._settings.amocrm_ready():
            return 0
        async with self._session_factory() as db:
            apps = (
                await db.scalars(
                    select(Application)
                    .where(
                        Application.crm_lead_id.is_not(None),
                        Application.status.in_(["new", "confirmed"]),
                    )
                    .order_by(Application.updated_at.asc())
                    .limit(limit)
                )
            ).all()
        processed = 0
        for app in apps:
            lead_id = str(app.crm_lead_id)
            event_key = f"reconcile:{lead_id}:{app.status}:{app.updated_at.isoformat()}"
            try:
                await self.process_lead_status_change(
                    lead_id=lead_id,
                    status_id=None,
                    event_key=event_key,
                )
                processed += 1
            except Exception:
                log_event(
                    logger,
                    action="crm.reconcile",
                    stage="lead",
                    result="error",
                    level=logging.ERROR,
                    lead_id=lead_id,
                )
        return processed

    async def _add_conflict_note(
        self, client: AmoCrmClient, lead_id: str, code: str
    ) -> None:
        text = (
            f"ExcursionAI: конфликт вместимости при подтверждении брони "
            f"({code}). Статус заявки в боте не изменён."
        )
        try:
            await client.add_lead_note(lead_id, text)
        except Exception:
            log_event(
                logger,
                action="crm.webhook",
                stage="conflict_note",
                result="error",
                level=logging.ERROR,
                lead_id=str(lead_id),
            )

    async def _event_seen(self, event_key: str) -> bool:
        async with self._session_factory() as db:
            existing = await db.scalar(
                select(CrmWebhookEvent).where(CrmWebhookEvent.event_key == event_key)
            )
            return existing is not None

    async def _store_event(
        self,
        event_key: str,
        lead_id: str,
        status_id: int | None,
        result: str,
    ) -> None:
        async with self._session_factory() as db:
            try:
                async with db.begin():
                    db.add(
                        CrmWebhookEvent(
                            event_key=event_key[:255],
                            lead_id=str(lead_id)[:128],
                            status_id=status_id,
                            result=result[:64],
                        )
                    )
            except IntegrityError:
                return


def extract_lead_status_events(payload: dict[str, Any]) -> list[tuple[str, int | None, str]]:
    """Извлекает (lead_id, status_id, event_key) из тела вебхука amoCRM."""
    events: list[tuple[str, int | None, str]] = []
    leads = payload.get("leads") or {}
    for kind in ("status", "update"):
        items = leads.get(kind) or []
        if isinstance(items, dict):
            items = [items]
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            lead_id = item.get("id")
            if lead_id is None:
                continue
            status_id = item.get("status_id")
            status_int = int(status_id) if status_id is not None else None
            updated = item.get("updated_at") or item.get("last_modified") or index
            event_key = f"{kind}:{lead_id}:{status_int}:{updated}"
            events.append((str(lead_id), status_int, event_key))
    return events
