from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError
from openai import AuthenticationError, BadRequestError, PermissionDeniedError

from excursion_ai.ai.errors import AIError, AIValidationError
from excursion_ai.ai.prompts import build_instructions, build_translation_instructions
from excursion_ai.ai.schemas import ConsultantReply, ExcursionTranslationsReply
from excursion_ai.catalog import Catalog
from excursion_ai.log_context import log_event

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelCallResult:
    reply: ConsultantReply
    duration_ms: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    model: str


@dataclass(frozen=True)
class TranslationCallResult:
    reply: ExcursionTranslationsReply
    duration_ms: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    model: str


def _usage_tokens(response: Any) -> tuple[int | None, int | None, int | None]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None, None, None
    return (
        getattr(usage, "input_tokens", None),
        getattr(usage, "output_tokens", None),
        getattr(usage, "total_tokens", None),
    )


class OpenAIConsultantClient:
    """Обёртка над AsyncOpenAI Responses API (parse + structured output)."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
        max_output_tokens: int,
        max_retries: int = 1,
    ) -> None:
        self._model = model
        self._timeout = timeout_seconds
        self._max_output_tokens = max_output_tokens
        self._client = AsyncOpenAI(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    @property
    def model(self) -> str:
        return self._model

    async def aclose(self) -> None:
        await self._client.close()

    async def consult(
        self,
        *,
        catalog: Catalog,
        interface_lang: str,
        input_messages: list[dict[str, str]],
    ) -> ModelCallResult:
        instructions = build_instructions(catalog, interface_lang)
        started = time.perf_counter()
        try:
            response = await self._client.responses.parse(
                model=self._model,
                instructions=instructions,
                input=input_messages,
                text_format=ConsultantReply,
                max_output_tokens=self._max_output_tokens,
                store=False,
            )
        except AuthenticationError as error:
            raise AIError("auth", detail=type(error).__name__) from error
        except PermissionDeniedError as error:
            raise AIError("permission", detail=type(error).__name__) from error
        except RateLimitError as error:
            raise AIError("rate_limit", detail=type(error).__name__) from error
        except APITimeoutError as error:
            raise AIError("timeout", detail=type(error).__name__) from error
        except APIConnectionError as error:
            raise AIError("connection", detail=type(error).__name__) from error
        except BadRequestError as error:
            raise AIError("bad_request", detail=type(error).__name__) from error
        except APIStatusError as error:
            status = getattr(error, "status_code", None)
            if status == 404:
                raise AIError("model_unavailable", detail="404") from error
            if status == 429:
                raise AIError("rate_limit", detail="429") from error
            raise AIError("api_error", detail=str(status or "status")) from error
        except Exception as error:
            raise AIError("api_error", detail=type(error).__name__) from error

        duration_ms = int((time.perf_counter() - started) * 1000)
        parsed = response.output_parsed
        if parsed is None:
            raise AIValidationError("invalid_response")
        if not isinstance(parsed, ConsultantReply):
            raise AIValidationError("invalid_response")
        if not parsed.answer.strip():
            raise AIValidationError("empty_answer")

        input_tokens, output_tokens, total_tokens = _usage_tokens(response)
        log_event(
            logger,
            action="ai.openai",
            stage="responses.parse",
            result="ok",
            model=self._model,
            duration_ms=duration_ms,
            input_tokens=input_tokens if input_tokens is not None else "-",
            output_tokens=output_tokens if output_tokens is not None else "-",
            total_tokens=total_tokens if total_tokens is not None else "-",
        )
        return ModelCallResult(
            reply=parsed,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            model=self._model,
        )

    async def translate_excursion(
        self,
        *,
        source_lang: str,
        target_langs: tuple[str, ...],
        title: str,
        description: str,
        route: list[str],
        meeting_point: str,
    ) -> TranslationCallResult:
        from excursion_ai.ai.prompts import build_translation_user_payload

        instructions = build_translation_instructions(
            source_lang=source_lang,
            target_langs=target_langs,
        )
        user_payload = build_translation_user_payload(
            source_lang=source_lang,
            target_langs=target_langs,
            title=title,
            description=description,
            route=route,
            meeting_point=meeting_point,
        )
        started = time.perf_counter()
        try:
            response = await self._client.responses.parse(
                model=self._model,
                instructions=instructions,
                input=[{"role": "user", "content": user_payload}],
                text_format=ExcursionTranslationsReply,
                max_output_tokens=self._max_output_tokens,
                store=False,
            )
        except AuthenticationError as error:
            raise AIError("auth", detail=type(error).__name__) from error
        except PermissionDeniedError as error:
            raise AIError("permission", detail=type(error).__name__) from error
        except RateLimitError as error:
            raise AIError("rate_limit", detail=type(error).__name__) from error
        except APITimeoutError as error:
            raise AIError("timeout", detail=type(error).__name__) from error
        except APIConnectionError as error:
            raise AIError("connection", detail=type(error).__name__) from error
        except BadRequestError as error:
            raise AIError("bad_request", detail=type(error).__name__) from error
        except APIStatusError as error:
            status = getattr(error, "status_code", None)
            if status == 404:
                raise AIError("model_unavailable", detail="404") from error
            if status == 429:
                raise AIError("rate_limit", detail="429") from error
            raise AIError("api_error", detail=str(status or "status")) from error
        except Exception as error:
            raise AIError("api_error", detail=type(error).__name__) from error

        duration_ms = int((time.perf_counter() - started) * 1000)
        parsed = response.output_parsed
        if parsed is None or not isinstance(parsed, ExcursionTranslationsReply):
            raise AIValidationError("invalid_response")
        input_tokens, output_tokens, total_tokens = _usage_tokens(response)
        log_event(
            logger,
            action="ai.openai",
            stage="translate.parse",
            result="ok",
            model=self._model,
            duration_ms=duration_ms,
            input_tokens=input_tokens if input_tokens is not None else "-",
            output_tokens=output_tokens if output_tokens is not None else "-",
            total_tokens=total_tokens if total_tokens is not None else "-",
        )
        return TranslationCallResult(
            reply=parsed,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            model=self._model,
        )
