"""Интеграция сохранённых заявок с amoCRM API v4."""

from __future__ import annotations

from excursion_ai.crm.payload import build_complex_lead_payload, deal_name_for
from excursion_ai.crm.pipelines import resolve_pipeline_target
from excursion_ai.crm.sync import ApplicationCrmSyncService, build_crm_sync_service

__all__ = [
    "ApplicationCrmSyncService",
    "build_complex_lead_payload",
    "build_crm_sync_service",
    "deal_name_for",
    "resolve_pipeline_target",
]
