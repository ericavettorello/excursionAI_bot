from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from excursion_ai.config import Settings
from excursion_ai.log_context import log_event, register_secret_value

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(settings: Settings) -> async_sessionmaker[AsyncSession]:
    global _engine, _session_factory
    if _session_factory is not None:
        return _session_factory

    url = settings.database_url()
    register_secret_value(settings.db_password)
    _engine = create_async_engine(
        url,
        echo=False,
        hide_parameters=True,
        pool_pre_ping=True,
    )
    _session_factory = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    log_event(
        logger,
        action="db.engine",
        stage="init",
        result="ok",
        db_name=settings.db_name,
        db_host=settings.db_host,
        db_port=settings.db_port,
    )
    return _session_factory


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Движок БД не инициализирован. Вызовите init_engine().")
    return _session_factory


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        log_event(
            logger,
            action="db.engine",
            stage="dispose",
            result="ok",
        )
    _engine = None
    _session_factory = None


async def check_connection(settings: Settings) -> dict[str, str]:
    """Проверяет доступ к БД и возвращает безопасные сведения без секретов."""
    factory = init_engine(settings)
    async with factory() as session:
        row = (
            await session.execute(
                text("SELECT current_database() AS db_name, current_user AS db_user")
            )
        ).mappings().one()
    return {"db_name": str(row["db_name"]), "db_user": str(row["db_user"])}
