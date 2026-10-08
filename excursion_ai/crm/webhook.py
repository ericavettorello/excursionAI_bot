"""HTTP endpoint для вебхуков amoCRM (рядом с polling Telegram)."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiohttp import web

from excursion_ai.config import Settings
from excursion_ai.crm.inbound import BookingInboundService, extract_lead_status_events
from excursion_ai.log_context import log_event

logger = logging.getLogger(__name__)

NotifyCallback = Callable[[Any], Awaitable[None]]


def create_webhook_app(
    settings: Settings,
    inbound: BookingInboundService,
    *,
    on_booking_result: NotifyCallback | None = None,
    include_webhook: bool = True,
) -> web.Application:
    """HTTP app: всегда /health; маршрут amoCRM — только при include_webhook."""
    app = web.Application()
    secret = (settings.amocrm_webhook_secret or "").strip()
    path = settings.amocrm_webhook_path or "/amocrm/webhook"

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def webhook(request: web.Request) -> web.Response:
        if secret:
            provided = request.query.get("secret") or request.headers.get(
                "X-ExcursionAI-Webhook-Secret", ""
            )
            if provided != secret:
                log_event(
                    logger,
                    action="crm.webhook",
                    stage="auth",
                    result="denied",
                    level=logging.WARNING,
                )
                return web.Response(status=401, text="unauthorized")

        content_type = request.content_type or ""
        payload: dict[str, Any]
        try:
            if "application/json" in content_type:
                raw = await request.json()
                payload = raw if isinstance(raw, dict) else {"leads": raw}
            else:
                form = await request.post()
                payload = _form_to_payload(form)
        except Exception:
            log_event(
                logger,
                action="crm.webhook",
                stage="parse",
                result="error",
                level=logging.ERROR,
            )
            return web.Response(status=400, text="bad_request")

        events = extract_lead_status_events(payload)
        log_event(
            logger,
            action="crm.webhook",
            stage="receive",
            result="ok",
            events=len(events),
        )
        for lead_id, status_id, event_key in events:
            try:
                result = await inbound.process_lead_status_change(
                    lead_id=lead_id,
                    status_id=status_id,
                    event_key=event_key,
                )
                if result is not None and on_booking_result is not None:
                    if result.notify_pending and not result.conflict:
                        await on_booking_result(result)
            except Exception:
                log_event(
                    logger,
                    action="crm.webhook",
                    stage="process",
                    result="error",
                    level=logging.ERROR,
                    lead_id=str(lead_id),
                )
        return web.Response(text="ok")

    app.router.add_get("/health", health)
    if include_webhook:
        app.router.add_route("*", path, webhook)
    return app


def _form_to_payload(form: Any) -> dict[str, Any]:
    """Упрощённый разбор form-urlencoded вебхуков amoCRM."""
    leads_status: list[dict[str, Any]] = []
    # Типичные ключи: leads[status][0][id], leads[status][0][status_id]
    buckets: dict[str, dict[str, Any]] = {}
    for key in form.keys():
        value = form.get(key)
        if not isinstance(key, str) or not key.startswith("leads["):
            continue
        # leads[status][0][id]
        parts = key.replace("]", "").split("[")
        if len(parts) < 4:
            continue
        kind, index, field = parts[1], parts[2], parts[3]
        bucket_key = f"{kind}:{index}"
        item = buckets.setdefault(bucket_key, {"_kind": kind})
        item[field] = value
    for item in buckets.values():
        kind = item.pop("_kind", "status")
        if kind == "status":
            leads_status.append(item)
        else:
            leads_status.append(item)
    return {"leads": {"status": leads_status, "update": []}}


async def start_webhook_site(
    settings: Settings,
    inbound: BookingInboundService,
    *,
    on_booking_result: NotifyCallback | None = None,
) -> web.AppRunner | None:
    """Всегда слушает /health (для Docker/nginx). Webhook — только если включён."""
    include_webhook = bool(settings.amocrm_webhook_enabled)
    if not include_webhook:
        log_event(
            logger,
            action="crm.webhook",
            stage="bootstrap",
            result="health_only",
        )
    app = create_webhook_app(
        settings,
        inbound,
        on_booking_result=on_booking_result,
        include_webhook=include_webhook,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(
        runner,
        host=settings.amocrm_webhook_host,
        port=settings.amocrm_webhook_port,
    )
    await site.start()
    log_event(
        logger,
        action="crm.webhook",
        stage="bootstrap",
        result="listening" if include_webhook else "listening_health_only",
        host=settings.amocrm_webhook_host,
        port=settings.amocrm_webhook_port,
        path=settings.amocrm_webhook_path if include_webhook else "/health",
    )
    return runner
