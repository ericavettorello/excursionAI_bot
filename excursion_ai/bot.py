from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import TelegramObject

from excursion_ai.ai.service import AIConsultantService
from excursion_ai.ai.translation import AdminTranslationService
from excursion_ai.catalog import Catalog, CatalogError
from excursion_ai.catalog_store import CatalogStore
from excursion_ai.config import Settings
from excursion_ai.crm.sync import ApplicationCrmSyncService
from excursion_ai.db.applications import ApplicationRepository
from excursion_ai.db.excursions import ExcursionRepository
from excursion_ai.db.sessions import SessionRepository
from excursion_ai.db.users import UserRepository
from excursion_ai.handlers import router
from excursion_ai.handlers.errors import router as errors_router
from excursion_ai.i18n import I18n
from excursion_ai.languages import LanguageStore
from excursion_ai.log_context import (
    get_event_id,
    log_event,
    new_event_id,
    reset_event_id,
    set_event_id,
)
from excursion_ai.logging_setup import setup_logging

logger = logging.getLogger(__name__)


class AppContextMiddleware(BaseMiddleware):
    """Передаёт зависимости в фильтры и обработчики.

    Важно: должен быть outer_middleware. Inner middleware вызывается
    только после успешной проверки фильтров, поэтому данные из него
    недоступны в MenuText и аналогичных фильтрах.
    """

    def __init__(
        self,
        i18n: I18n,
        catalog: Catalog,
        langs: LanguageStore,
        applications: ApplicationRepository | None = None,
        users: UserRepository | None = None,
        ai: AIConsultantService | None = None,
        settings: Settings | None = None,
        catalog_store: CatalogStore | None = None,
        excursions: ExcursionRepository | None = None,
        admin_translation: AdminTranslationService | None = None,
        crm_sync: ApplicationCrmSyncService | None = None,
        sessions: SessionRepository | None = None,
    ) -> None:
        self.i18n = i18n
        self.catalog = catalog
        self.langs = langs
        self.applications = applications
        self.users = users
        self.ai = ai
        self.settings = settings
        self.catalog_store = catalog_store
        self.excursions = excursions
        self.admin_translation = admin_translation
        self.crm_sync = crm_sync
        self.sessions = sessions

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["i18n"] = self.i18n
        data["langs"] = self.langs
        if self.settings is not None:
            data["settings"] = self.settings
        if self.catalog_store is not None:
            data["catalog_store"] = self.catalog_store
            try:
                data["catalog"] = await self.catalog_store.get_published()
            except CatalogError as error:
                log_event(
                    logger,
                    action="catalog.cache",
                    stage="middleware",
                    result="error",
                    level=logging.ERROR,
                    error_type=type(error).__name__,
                )
                data["catalog"] = self.catalog
        else:
            data["catalog"] = self.catalog
        if self.excursions is not None:
            data["excursions"] = self.excursions
        if self.applications is not None:
            data["applications"] = self.applications
        if self.users is not None:
            data["users"] = self.users
        if self.ai is not None:
            data["ai"] = self.ai
        if self.admin_translation is not None:
            data["admin_translation"] = self.admin_translation
        if self.crm_sync is not None:
            data["crm_sync"] = self.crm_sync
        if self.sessions is not None:
            data["sessions"] = self.sessions
        return await handler(event, data)


class EventContextMiddleware(BaseMiddleware):
    """Создаёт event_id и пишет одну запись о входящем событии без PII."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        existing = data.get("event_id")
        event_id = existing if isinstance(existing, str) and existing != "-" else new_event_id()
        token = set_event_id(event_id)
        data["event_id"] = event_id

        payload = "none"
        if getattr(event, "text", None):
            text = event.text or ""
            payload = "command" if text.startswith("/") else "text"
        elif getattr(event, "data", None):
            payload = "callback"

        log_event(
            logger,
            action="event.receive",
            stage="accepted",
            result="ok",
            event_type=type(event).__name__,
            payload=payload,
        )
        try:
            return await handler(event, data)
        finally:
            reset_event_id(token)


def create_dispatcher(
    i18n: I18n,
    catalog: Catalog,
    langs: LanguageStore,
    applications: ApplicationRepository | None = None,
    users: UserRepository | None = None,
    ai: AIConsultantService | None = None,
    settings: Settings | None = None,
    catalog_store: CatalogStore | None = None,
    excursions: ExcursionRepository | None = None,
    admin_translation: AdminTranslationService | None = None,
    crm_sync: ApplicationCrmSyncService | None = None,
    sessions: SessionRepository | None = None,
) -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())
    context = AppContextMiddleware(
        i18n,
        catalog,
        langs,
        applications=applications,
        users=users,
        ai=ai,
        settings=settings,
        catalog_store=catalog_store,
        excursions=excursions,
        admin_translation=admin_translation,
        crm_sync=crm_sync,
        sessions=sessions,
    )
    events = EventContextMiddleware()
    dispatcher.message.outer_middleware(context)
    dispatcher.callback_query.outer_middleware(context)
    dispatcher.errors.outer_middleware(context)
    dispatcher.message.outer_middleware(events)
    dispatcher.callback_query.outer_middleware(events)
    dispatcher.include_router(errors_router)
    dispatcher.include_router(router)
    return dispatcher


def create_bot(token: str) -> Bot:
    """Создаёт бота с HTTP-сессией, предпочитающей IPv4.

    На части Windows-сетей резолв api.telegram.org по IPv6 даёт
    getaddrinfo failed / нестабильный DNS — принудительный AF_INET стабильнее.
    """
    import socket

    from aiogram.client.session.aiohttp import AiohttpSession

    session = AiohttpSession()
    session._connector_init["family"] = socket.AF_INET
    return Bot(token=token, session=session)


__all__ = [
    "AppContextMiddleware",
    "EventContextMiddleware",
    "create_bot",
    "create_dispatcher",
    "get_event_id",
    "setup_logging",
]
