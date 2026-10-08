from __future__ import annotations

import asyncio
import logging
import time

from excursion_ai.catalog import Catalog, CatalogError
from excursion_ai.db.excursions import ExcursionRepository
from excursion_ai.log_context import log_event

logger = logging.getLogger(__name__)


class CatalogStore:
    """Кеш опубликованного каталога из PostgreSQL с инвалидацией."""

    def __init__(
        self,
        repository: ExcursionRepository,
        *,
        ttl_seconds: int = 60,
    ) -> None:
        self._repository = repository
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()
        self._catalog: Catalog | None = None
        self._loaded_at = 0.0
        self._generation = 0

    def invalidate(self) -> None:
        self._generation += 1
        self._catalog = None
        self._loaded_at = 0.0
        log_event(
            logger,
            action="catalog.cache",
            stage="invalidate",
            result="ok",
            generation=self._generation,
        )

    async def get_published(self, *, force: bool = False) -> Catalog:
        now = time.monotonic()
        cached = self._catalog
        if (
            not force
            and cached is not None
            and (now - self._loaded_at) < self._ttl
        ):
            return cached
        async with self._lock:
            now = time.monotonic()
            cached = self._catalog
            if (
                not force
                and cached is not None
                and (now - self._loaded_at) < self._ttl
            ):
                return cached
            try:
                catalog = await self._repository.published_catalog()
            except Exception as error:
                log_event(
                    logger,
                    action="catalog.cache",
                    stage="reload",
                    result="error",
                    level=logging.ERROR,
                    error_type=type(error).__name__,
                )
                if self._catalog is not None:
                    # Только ранее загруженный снимок из БД; JSON не подставляем.
                    return self._catalog
                raise CatalogError(
                    "Не удалось загрузить каталог из PostgreSQL. "
                    "JSON не подставляется автоматически."
                ) from error
            self._catalog = catalog
            self._loaded_at = time.monotonic()
            log_event(
                logger,
                action="catalog.cache",
                stage="reload",
                result="ok",
                count=len(catalog.all()),
                generation=self._generation,
            )
            return catalog
