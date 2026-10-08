"""Уведомления клиенту о подтверждении / отмене брони."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from excursion_ai.db.booking import BookingResult, BookingService
from excursion_ai.db.models import ClientNotifyKind
from excursion_ai.db.public_number import format_public_number
from excursion_ai.db.sessions import format_rome_date, format_rome_time
from excursion_ai.i18n import I18n
from excursion_ai.log_context import log_event

logger = logging.getLogger(__name__)


class BookingNotifier:
    def __init__(
        self,
        bot: Bot,
        i18n: I18n,
        booking: BookingService,
    ) -> None:
        self._bot = bot
        self._i18n = i18n
        self._booking = booking

    async def notify_result(self, result: BookingResult) -> None:
        if result.conflict or not result.notify_pending:
            return
        application = result.application
        kind = application.client_notify_kind
        if kind not in {
            ClientNotifyKind.CONFIRMED.value,
            ClientNotifyKind.CANCELLED.value,
        }:
            return
        telegram_id = await self._booking.telegram_user_id_for(application)
        if telegram_id is None:
            await self._booking.mark_notify_error(application.id)
            return

        lang = application.language or "ru"
        number = format_public_number(int(application.public_number))
        starts = application.session_starts_at
        day = format_rome_date(starts) if starts else format_rome_date(
            application.requested_date  # type: ignore[arg-type]
        )
        hhmm = format_rome_time(starts) if starts else "—"
        if kind == ClientNotifyKind.CONFIRMED.value:
            text = self._i18n.t(
                lang,
                "booking.confirmed",
                number=number,
                title=application.excursion_title,
                date=day,
                time=hhmm,
                count=application.participants_count,
            )
        else:
            text = self._i18n.t(
                lang,
                "booking.cancelled",
                number=number,
                title=application.excursion_title,
                date=day,
                time=hhmm,
                count=application.participants_count,
            )
        try:
            await self._bot.send_message(telegram_id, text)
            await self._booking.mark_notify_sent(application.id, kind=kind)
            log_event(
                logger,
                action="booking.notify",
                stage=kind,
                result="sent",
                application_id=str(application.id),
            )
        except TelegramAPIError:
            await self._booking.mark_notify_error(application.id)
            log_event(
                logger,
                action="booking.notify",
                stage=kind,
                result="error",
                level=logging.ERROR,
                application_id=str(application.id),
            )

    async def retry_pending(self, *, limit: int = 20) -> int:
        items = await self._booking.list_notify_retry(limit=limit)
        for app in items:
            result = BookingResult(
                application=app,
                changed=False,
                conflict=False,
                notify_pending=True,
                code="retry",
            )
            await self.notify_result(result)
        return len(items)
