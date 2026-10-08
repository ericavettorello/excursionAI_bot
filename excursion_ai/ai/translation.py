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
    AIValidationError,
)
from excursion_ai.ai.schemas import ExcursionTranslationsReply, TranslatedLanguageBlock
from excursion_ai.catalog import Excursion
from excursion_ai.config import SUPPORTED_LANGS, Settings
from excursion_ai.db.ai_usage import AIUsageRepository
from excursion_ai.db.excursions import (
    REVIEW_PENDING,
    ExcursionRepository,
    ExcursionRepositoryError,
)
from excursion_ai.db.translation_meta import fingerprint_translation_block, other_languages
from excursion_ai.log_context import log_event

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TranslationServiceResult:
    excursion: Excursion
    target_langs: tuple[str, ...]
    duration_ms: int
    model: str
    input_tokens: int | None
    output_tokens: int | None


class AdminTranslationService:
    """Автоперевод текстов экскурсии с отдельным админ-лимитом."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: OpenAIConsultantClient | None,
        usage: AIUsageRepository,
        excursions: ExcursionRepository,
    ) -> None:
        self._settings = settings
        self._client = client
        self._usage = usage
        self._excursions = excursions
        self._locks: dict[str, asyncio.Lock] = {}

    def is_available(self) -> bool:
        return bool(
            self._settings.ai_enabled
            and self._settings.openai_api_key
            and self._settings.openai_model
            and self._client is not None
        )

    def missing_settings(self) -> list[str]:
        return self._settings.ai_missing_settings()

    def _lock_for(self, excursion_id: str) -> asyncio.Lock:
        lock = self._locks.get(excursion_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[excursion_id] = lock
        return lock

    def _validate_reply(
        self,
        reply: ExcursionTranslationsReply,
        *,
        target_langs: tuple[str, ...],
        source_route_len: int,
    ) -> dict[str, TranslatedLanguageBlock]:
        by_lang: dict[str, TranslatedLanguageBlock] = {}
        for block in reply.translations:
            code = block.language.strip().lower()
            if code not in target_langs:
                raise AIValidationError("unexpected_language")
            if code in by_lang:
                raise AIValidationError("duplicate_language")
            if len(block.route) != source_route_len:
                raise AIValidationError("route_length_mismatch")
            for item in block.route:
                if not str(item).strip():
                    raise AIValidationError("empty_route_item")
            by_lang[code] = block
        if set(by_lang) != set(target_langs):
            raise AIValidationError("missing_language")
        return by_lang

    async def translate_from_source(
        self,
        *,
        telegram_user_id: int,
        excursion_id: str,
        source_fingerprint: str,
        force_overwrite: bool = False,
    ) -> TranslationServiceResult:
        if not self.is_available() or self._client is None:
            raise AIConfigError("not_configured")

        lock = self._lock_for(excursion_id)
        if lock.locked():
            raise AIBusyError("busy")

        async with lock:
            excursion = await self._excursions.get(excursion_id)
            if excursion is None:
                raise ExcursionRepositoryError("not_found")
            source_lang = excursion.source_language
            if source_lang not in SUPPORTED_LANGS:
                raise AIValidationError("no_source_language")
            source_block = excursion.translations.get(source_lang)
            if not source_block:
                raise AIValidationError("no_source_text")
            current_fp = fingerprint_translation_block(source_block)
            if current_fp != source_fingerprint:
                raise AIValidationError("source_changed")

            targets = other_languages(source_lang)
            if not force_overwrite:
                protected: list[str] = []
                for code in targets:
                    if not self._excursions.translation_complete(excursion, code):
                        continue
                    meta = excursion.meta_for(code)
                    if meta.review_status == "confirmed" or meta.origin == "manual":
                        protected.append(code)
                if protected:
                    raise AIValidationError("overwrite_confirmation_required")

            await self._usage.try_reserve_admin_translation(
                telegram_user_id=telegram_user_id,
                daily_limit=self._settings.ai_admin_translation_daily_limit,
            )

            title = str(source_block["title"])
            description = str(source_block["description"])
            route = [str(item) for item in source_block["route"]]  # type: ignore[index]
            meeting = str(source_block["meeting_point"])

            call = await self._client.translate_excursion(
                source_lang=source_lang,
                target_langs=targets,
                title=title,
                description=description,
                route=route,
                meeting_point=meeting,
            )

            # Re-check source after API wait (no open DB txn during call).
            latest = await self._excursions.get(excursion_id)
            if latest is None:
                raise ExcursionRepositoryError("not_found")
            latest_block = latest.translations.get(source_lang)
            if not latest_block:
                raise AIValidationError("no_source_text")
            if fingerprint_translation_block(latest_block) != source_fingerprint:
                raise AIValidationError("source_changed")

            validated = self._validate_reply(
                call.reply,
                target_langs=targets,
                source_route_len=len(route),
            )
            city_label = str(latest_block.get("city") or latest.city_id)
            for code, block in validated.items():
                await self._excursions.upsert_translation(
                    excursion_id=excursion_id,
                    language=code,
                    title=block.title.strip(),
                    city_label=city_label,
                    description=block.description.strip(),
                    route=[item.strip() for item in block.route],
                    meeting_point=block.meeting_point.strip(),
                    apply_immediately=latest.status != "published",
                    review_status=REVIEW_PENDING,
                    origin="ai",
                    source_fingerprint=source_fingerprint,
                )

            updated = await self._excursions.get(excursion_id)
            assert updated is not None
            log_event(
                logger,
                action="ai.translate",
                stage="complete",
                result="ok",
                excursion_id=excursion_id,
                model=call.model,
                duration_ms=call.duration_ms,
                input_tokens=call.input_tokens if call.input_tokens is not None else "-",
                output_tokens=call.output_tokens
                if call.output_tokens is not None
                else "-",
            )
            return TranslationServiceResult(
                excursion=updated,
                target_langs=targets,
                duration_ms=call.duration_ms,
                model=call.model,
                input_tokens=call.input_tokens,
                output_tokens=call.output_tokens,
            )


def build_admin_translation_service(
    settings: Settings,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    excursions: ExcursionRepository,
    client: OpenAIConsultantClient | None = None,
) -> AdminTranslationService:
    if client is None and settings.ai_enabled and settings.openai_api_key and settings.openai_model:
        client = OpenAIConsultantClient(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            timeout_seconds=settings.ai_timeout_seconds,
            max_output_tokens=max(settings.ai_max_output_tokens, 1200),
            max_retries=settings.ai_max_retries,
        )
    return AdminTranslationService(
        settings,
        client=client,
        usage=AIUsageRepository(session_factory),
        excursions=excursions,
    )
