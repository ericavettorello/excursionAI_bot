from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from excursion_ai.ai.client import OpenAIConsultantClient
from excursion_ai.ai.errors import (
    AIBusyError,
    AIConfigError,
    AIError,
    AILimitError,
    AIUnavailableError,
    AIValidationError,
)
from excursion_ai.ai.schemas import ConsultantReply
from excursion_ai.ai.session import ConversationStore
from excursion_ai.catalog import Catalog, Excursion
from excursion_ai.catalog_store import CatalogStore
from excursion_ai.config import Settings
from excursion_ai.db.ai_usage import AIUsageRepository
from excursion_ai.log_context import log_event

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConsultationResult:
    answer: str
    recommended: list[Excursion]
    model: str
    duration_ms: int
    rejected_ids: list[str]


class AIConsultantService:
    def __init__(
        self,
        settings: Settings,
        *,
        catalog: Catalog | None = None,
        catalog_store: CatalogStore | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        client: OpenAIConsultantClient | None = None,
        conversations: ConversationStore | None = None,
        usage: AIUsageRepository | None = None,
    ) -> None:
        self._settings = settings
        self._catalog = catalog
        self._catalog_store = catalog_store
        self._conversations = conversations or ConversationStore()
        self._usage = usage or (
            AIUsageRepository(session_factory) if session_factory is not None else None
        )
        self._client = client
        self._locks: dict[int, asyncio.Lock] = {}
        self._missing = self._collect_missing()

    def _collect_missing(self) -> list[str]:
        missing: list[str] = []
        if not self._settings.ai_enabled:
            return ["AI_ENABLED"]
        if not self._settings.openai_api_key:
            missing.append("OPENAI_API_KEY")
        if not self._settings.openai_model:
            missing.append("OPENAI_MODEL")
        return missing

    @property
    def missing_settings(self) -> list[str]:
        return list(self._missing)

    def is_available(self) -> bool:
        return not self._missing and self._client is not None and self._usage is not None

    @property
    def client(self) -> OpenAIConsultantClient | None:
        """Общий OpenAI-клиент для консультанта и админ-переводов."""
        return self._client

    def clear_dialog(self, telegram_user_id: int) -> None:
        self._conversations.clear(telegram_user_id)

    def _lock_for(self, telegram_user_id: int) -> asyncio.Lock:
        lock = self._locks.get(telegram_user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[telegram_user_id] = lock
        return lock

    async def _current_catalog(self) -> Catalog:
        if self._catalog_store is not None:
            return await self._catalog_store.get_published()
        if self._catalog is not None:
            return self._catalog
        return Catalog([])

    def _filter_ids(
        self,
        catalog: Catalog,
        ids: list[str],
    ) -> tuple[list[Excursion], list[str]]:
        recommended: list[Excursion] = []
        rejected: list[str] = []
        seen: set[str] = set()
        for raw_id in ids:
            excursion_id = str(raw_id).strip()
            if not excursion_id or excursion_id in seen:
                continue
            seen.add(excursion_id)
            excursion = catalog.get(excursion_id)
            if excursion is None or excursion.status != "published":
                rejected.append(excursion_id)
                continue
            recommended.append(excursion)
        return recommended, rejected

    async def ask(
        self,
        *,
        telegram_user_id: int,
        interface_lang: str,
        user_text: str,
    ) -> ConsultationResult:
        if not self._settings.ai_enabled:
            raise AIUnavailableError("disabled")
        if self._missing or self._client is None or self._usage is None:
            raise AIConfigError("not_configured")

        text = user_text.strip()
        if not text:
            raise AIValidationError("empty_input")
        if len(text) > self._settings.ai_max_input_chars:
            raise AIValidationError("input_too_long")

        lock = self._lock_for(telegram_user_id)
        if lock.locked():
            raise AIBusyError()

        async with lock:
            await self._usage.try_reserve(
                telegram_user_id=telegram_user_id,
                user_limit=self._settings.ai_daily_user_limit,
                global_limit=self._settings.ai_daily_global_limit,
            )
            # Актуальный каталог на каждый запрос; история не важнее данных.
            catalog = await self._current_catalog()
            history_note = (
                "\n\n[System note for this turn only: use the current catalogue "
                "from instructions. Ignore outdated prices or titles from earlier "
                "assistant messages if they conflict with the catalogue.]"
            )
            messages = self._conversations.build_input_messages(
                telegram_user_id,
                text + history_note,
            )
            try:
                call = await self._client.consult(
                    catalog=catalog,
                    interface_lang=interface_lang,
                    input_messages=messages,
                )
            except AIError:
                raise
            except Exception as error:
                raise AIError("api_error", detail=type(error).__name__) from error

            reply: ConsultantReply = call.reply
            recommended, rejected = self._filter_ids(
                catalog,
                reply.recommended_excursion_ids,
            )
            if rejected:
                log_event(
                    logger,
                    action="ai.filter",
                    stage="unknown_ids",
                    result="rejected",
                    rejected_count=len(rejected),
                )

            self._conversations.add_pair(telegram_user_id, text, reply.answer.strip())
            log_event(
                logger,
                action="ai.consult",
                stage="complete",
                result="ok",
                model=call.model,
                duration_ms=call.duration_ms,
                recommended_count=len(recommended),
                input_tokens=call.input_tokens if call.input_tokens is not None else "-",
                output_tokens=call.output_tokens if call.output_tokens is not None else "-",
            )
            return ConsultationResult(
                answer=reply.answer.strip(),
                recommended=recommended,
                model=call.model,
                duration_ms=call.duration_ms,
                rejected_ids=rejected,
            )


def build_ai_service(
    settings: Settings,
    *,
    catalog: Catalog | None = None,
    catalog_store: CatalogStore | None = None,
    session_factory: async_sessionmaker[AsyncSession],
) -> AIConsultantService:
    client: OpenAIConsultantClient | None = None
    if settings.ai_enabled and settings.openai_api_key and settings.openai_model:
        client = OpenAIConsultantClient(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            timeout_seconds=settings.ai_timeout_seconds,
            max_output_tokens=settings.ai_max_output_tokens,
            max_retries=settings.ai_max_retries,
        )
    return AIConsultantService(
        settings,
        catalog=catalog,
        catalog_store=catalog_store,
        session_factory=session_factory,
        client=client,
        usage=AIUsageRepository(session_factory),
    )
