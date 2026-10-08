from __future__ import annotations

import asyncio
import logging
import sys

from excursion_ai.ai.service import build_ai_service
from excursion_ai.ai.translation import build_admin_translation_service
from excursion_ai.bot import create_bot, create_dispatcher
from excursion_ai.catalog import CatalogError
from excursion_ai.catalog_store import CatalogStore
from excursion_ai.config import ConfigError, load_settings
from excursion_ai.console import configure_console
from excursion_ai.crm.sync import build_crm_sync_service
from excursion_ai.crm.inbound import BookingInboundService
from excursion_ai.crm.notify import BookingNotifier
from excursion_ai.crm.webhook import start_webhook_site
from excursion_ai.db.applications import ApplicationRepository
from excursion_ai.db.booking import BookingService
from excursion_ai.db.excursions import ExcursionRepository
from excursion_ai.db.session import check_connection, dispose_engine, init_engine
from excursion_ai.db.seed_sessions import ensure_demo_schedules
from excursion_ai.db.sessions import SessionRepository
from excursion_ai.db.users import UserRepository
from excursion_ai.i18n import I18nError, load_i18n
from excursion_ai.languages import LanguageStore
from excursion_ai.log_context import log_event
from excursion_ai.logging_setup import setup_logging
from excursion_ai.validate import validate_project_data

logger = logging.getLogger(__name__)


async def run_bot() -> None:
    settings = load_settings(require_db=True)
    i18n = load_i18n()

    session_factory = init_engine(settings)
    info = await check_connection(settings)
    log_event(
        logger,
        action="db.connect",
        stage="check",
        result="ok",
        db_name=info["db_name"],
        db_user=info["db_user"],
    )

    users = UserRepository(session_factory)
    applications = ApplicationRepository(session_factory)
    excursions = ExcursionRepository(session_factory)
    sessions = SessionRepository(session_factory)
    booking = BookingService(session_factory, sessions)
    catalog_store = CatalogStore(
        excursions,
        ttl_seconds=settings.catalog_cache_ttl_seconds,
    )
    catalog = await catalog_store.get_published()
    seed_result = await ensure_demo_schedules(
        excursions=excursions,
        sessions=sessions,
    )
    log_event(
        logger,
        action="app.bootstrap",
        stage="data_loaded",
        result="ok",
        locales=3,
        excursions=len(catalog.all()),
        schedule_seeded=seed_result.excursions_seeded,
        schedule_created=seed_result.sessions_created,
    )

    langs = LanguageStore(users)
    ai = build_ai_service(
        settings,
        catalog=catalog,
        catalog_store=catalog_store,
        session_factory=session_factory,
    )
    admin_translation = build_admin_translation_service(
        settings,
        session_factory=session_factory,
        excursions=excursions,
        client=ai.client,
    )
    crm_sync = build_crm_sync_service(settings, applications)
    inbound = BookingInboundService(settings, session_factory, booking)
    log_event(
        logger,
        action="ai.bootstrap",
        stage="init",
        result="ready" if ai.is_available() else "not_ready",
        missing=",".join(ai.missing_settings) or "-",
    )
    log_event(
        logger,
        action="ai.translate",
        stage="init",
        result="ready" if admin_translation.is_available() else "not_ready",
        missing=",".join(admin_translation.missing_settings()) or "-",
    )
    log_event(
        logger,
        action="crm.bootstrap",
        stage="init",
        result="ready" if crm_sync.is_available() else "not_ready",
        missing=",".join(crm_sync.missing_settings()) or "-",
    )

    bot = create_bot(settings.bot_token)
    notifier = BookingNotifier(bot, i18n, booking)
    dispatcher = create_dispatcher(
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
    log_event(
        logger,
        action="app.start",
        stage="polling",
        result="starting",
    )
    webhook_runner = None
    try:
        webhook_runner = await start_webhook_site(
            settings,
            inbound,
            on_booking_result=notifier.notify_result,
        )
        if crm_sync.is_available():
            retried = await crm_sync.retry_pending(limit=20)
            log_event(
                logger,
                action="crm.sync",
                stage="startup_retry",
                result="ok",
                count=retried,
            )
            await notifier.retry_pending(limit=20)
        await _start_polling_with_retries(dispatcher, bot)
    finally:
        if webhook_runner is not None:
            await webhook_runner.cleanup()
        await inbound.aclose()
        await crm_sync.aclose()
        await dispose_engine()
        log_event(
            logger,
            action="app.stop",
            stage="polling",
            result="stopped",
        )


async def _start_polling_with_retries(dispatcher, bot) -> None:
    from aiogram.exceptions import TelegramNetworkError

    attempts = 5
    for attempt in range(1, attempts + 1):
        try:
            await dispatcher.start_polling(bot)
            return
        except TelegramNetworkError as error:
            log_event(
                logger,
                action="app.start",
                stage="polling",
                result="network_error",
                level=logging.ERROR,
                attempt=attempt,
                error_type=type(error).__name__,
            )
            if attempt >= attempts:
                raise
            delay = min(2 * attempt, 10)
            print(
                f"Нет связи с api.telegram.org (попытка {attempt}/{attempts}). "
                f"Повтор через {delay} с…"
            )
            await asyncio.sleep(delay)


def main() -> None:
    configure_console()
    setup_logging()
    log_event(
        logger,
        action="app.start",
        stage="init",
        result="ok",
    )
    try:
        validate_project_data()
        log_event(
            logger,
            action="app.bootstrap",
            stage="validate",
            result="ok",
        )
        asyncio.run(run_bot())
    except ConfigError as error:
        log_event(
            logger,
            action="app.start",
            stage="config",
            result="error",
            level=logging.ERROR,
            error_type=type(error).__name__,
        )
        print(error)
        sys.exit(1)
    except (I18nError, CatalogError) as error:
        log_event(
            logger,
            action="app.bootstrap",
            stage="data",
            result="error",
            level=logging.ERROR,
            error_type=type(error).__name__,
        )
        print(f"Ошибка данных проекта: {error}")
        sys.exit(1)
    except Exception as error:
        from aiogram.exceptions import TelegramNetworkError

        from excursion_ai.log_context import log_exception

        if isinstance(error, ModuleNotFoundError):
            missing = getattr(error, "name", None) or str(error)
            log_event(
                logger,
                action="app.start",
                stage="deps",
                result="error",
                level=logging.ERROR,
                error_type=type(error).__name__,
            )
            print(
                f"Не найден пакет Python: {missing}\n"
                "Запускайте бота из виртуального окружения проекта:\n"
                "  .\\.venv\\Scripts\\python.exe -m excursion_ai\n"
                "Если окружение ещё не собрано:\n"
                "  .\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt"
            )
            sys.exit(1)

        if isinstance(error, TelegramNetworkError):
            log_event(
                logger,
                action="app.start",
                stage="polling",
                result="network_error",
                level=logging.ERROR,
                error_type=type(error).__name__,
            )
            print(
                "Не удалось подключиться к Telegram API (api.telegram.org).\n"
                "Проверьте интернет, DNS, VPN/прокси и антивирус, затем запустите снова:\n"
                "  .\\.venv\\Scripts\\python.exe -m excursion_ai"
            )
            sys.exit(1)

        log_exception(
            logger,
            action="app.start",
            stage="fatal",
            exc=error,
            result="error",
        )
        raise


if __name__ == "__main__":
    main()
