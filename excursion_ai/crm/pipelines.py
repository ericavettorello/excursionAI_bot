"""Поиск воронки и этапа amoCRM по ID или имени из настроек."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from excursion_ai.config import Settings
from excursion_ai.crm.client import AmoCrmApiError, AmoCrmClient


@dataclass(frozen=True)
class PipelineTarget:
    pipeline_id: int
    status_id: int
    pipeline_name: str
    status_name: str


def _norm(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def find_pipeline_target(
    pipelines: list[dict[str, Any]],
    settings: Settings,
) -> PipelineTarget:
    """Выбирает воронку/этап. Без совпадения — ошибка, без fallback."""
    if settings.amocrm_pipeline_id is not None and settings.amocrm_status_id is not None:
        return _target_by_ids(
            pipelines,
            pipeline_id=settings.amocrm_pipeline_id,
            status_id=settings.amocrm_status_id,
        )

    pipeline_name = (settings.amocrm_pipeline_name or "").strip()
    status_name = (settings.amocrm_status_name or "").strip()
    if not pipeline_name or not status_name:
        raise AmoCrmApiError("pipeline_config_missing")

    return _target_by_names(
        pipelines,
        pipeline_name=pipeline_name,
        status_name=status_name,
    )


def _target_by_ids(
    pipelines: list[dict[str, Any]],
    *,
    pipeline_id: int,
    status_id: int,
) -> PipelineTarget:
    for pipeline in pipelines:
        if int(pipeline.get("id") or 0) != pipeline_id:
            continue
        statuses = (pipeline.get("_embedded") or {}).get("statuses") or []
        for status in statuses:
            if int(status.get("id") or 0) != status_id:
                continue
            return PipelineTarget(
                pipeline_id=pipeline_id,
                status_id=status_id,
                pipeline_name=str(pipeline.get("name") or ""),
                status_name=str(status.get("name") or ""),
            )
        raise AmoCrmApiError("status_not_found")
    raise AmoCrmApiError("pipeline_not_found")


def _target_by_names(
    pipelines: list[dict[str, Any]],
    *,
    pipeline_name: str,
    status_name: str,
) -> PipelineTarget:
    wanted_pipeline = _norm(pipeline_name)
    wanted_status = _norm(status_name)
    matched_pipeline: dict[str, Any] | None = None
    for pipeline in pipelines:
        if _norm(str(pipeline.get("name") or "")) == wanted_pipeline:
            matched_pipeline = pipeline
            break
    if matched_pipeline is None:
        raise AmoCrmApiError("pipeline_not_found")

    statuses = (matched_pipeline.get("_embedded") or {}).get("statuses") or []
    for status in statuses:
        if _norm(str(status.get("name") or "")) == wanted_status:
            return PipelineTarget(
                pipeline_id=int(matched_pipeline["id"]),
                status_id=int(status["id"]),
                pipeline_name=str(matched_pipeline.get("name") or ""),
                status_name=str(status.get("name") or ""),
            )
    raise AmoCrmApiError("status_not_found")


async def resolve_pipeline_target(
    client: AmoCrmClient,
    settings: Settings,
) -> PipelineTarget:
    pipelines = await client.list_pipelines()
    return find_pipeline_target(pipelines, settings)


def find_status_ids_by_names(
    pipelines: list[dict[str, Any]],
    *,
    pipeline_name: str,
    confirmed_name: str,
    cancelled_name: str,
) -> dict[str, int]:
    wanted_pipeline = _norm(pipeline_name)
    matched: dict[str, Any] | None = None
    for pipeline in pipelines:
        if _norm(str(pipeline.get("name") or "")) == wanted_pipeline:
            matched = pipeline
            break
    if matched is None:
        raise AmoCrmApiError("booking_pipeline_not_found")

    statuses = (matched.get("_embedded") or {}).get("statuses") or []
    by_name = {_norm(str(s.get("name") or "")): int(s["id"]) for s in statuses}
    confirmed = by_name.get(_norm(confirmed_name))
    cancelled = by_name.get(_norm(cancelled_name))
    if confirmed is None:
        raise AmoCrmApiError("booking_status_confirmed_not_found")
    if cancelled is None:
        raise AmoCrmApiError("booking_status_cancelled_not_found")
    return {"confirmed": confirmed, "cancelled": cancelled}


async def resolve_booking_status_ids(
    client: AmoCrmClient,
    settings: Settings,
) -> dict[str, int]:
    pipelines = await client.list_pipelines()
    pipeline_name = (
        settings.amocrm_pipeline_name.strip()
        or settings.amocrm_booking_pipeline_name.strip()
        or "ЭКСКУРСИИ"
    )
    confirmed = (
        settings.amocrm_status_name_confirmed.strip() or "Бронь подтверждена"
    )
    cancelled = (
        settings.amocrm_status_name_cancelled.strip() or "Бронь отменена"
    )
    return find_status_ids_by_names(
        pipelines,
        pipeline_name=pipeline_name,
        confirmed_name=confirmed,
        cancelled_name=cancelled,
    )
