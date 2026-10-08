from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from excursion_ai.config import SUPPORTED_LANGS
from excursion_ai.db.models import Application, ApplicationStatus, CrmSyncStatus, User
from excursion_ai.db.sessions import SessionCapacityError, SessionRepository, SessionValidationError
from excursion_ai.log_context import log_event

logger = logging.getLogger(__name__)


class ApplicationError(Exception):
    """Ошибка операций с заявками без персональных данных."""


class ApplicationOwnershipError(ApplicationError):
    """Заявка принадлежит другому пользователю."""


class ApplicationValidationError(ApplicationError):
    """Некорректные данные заявки."""


@dataclass(frozen=True)
class ApplicationCreateData:
    submission_key: str
    excursion_id: str
    excursion_title: str
    language: str
    requested_date: date
    participants_count: int
    customer_name: str
    unit_price: Decimal
    currency: str
    phone: str | None = None
    is_demo: bool = True
    session_id: uuid.UUID | None = None
    session_starts_at: datetime | None = None


class ApplicationRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    def _validate(self, data: ApplicationCreateData) -> None:
        if not data.submission_key.strip():
            raise ApplicationValidationError("submission_key_required")
        if data.language not in SUPPORTED_LANGS:
            raise ApplicationValidationError("unsupported_language")
        if data.participants_count <= 0:
            raise ApplicationValidationError("participants_must_be_positive")
        if data.unit_price < 0:
            raise ApplicationValidationError("unit_price_must_be_nonnegative")
        if not data.excursion_id.strip() or not data.excursion_title.strip():
            raise ApplicationValidationError("excursion_required")
        if not data.customer_name.strip():
            raise ApplicationValidationError("customer_name_required")
        if not data.currency.strip():
            raise ApplicationValidationError("currency_required")
        if data.session_id is None or data.session_starts_at is None:
            raise ApplicationValidationError("session_required")

    async def create_application(
        self,
        *,
        telegram_user_id: int,
        data: ApplicationCreateData,
        crm_sync_status: str = CrmSyncStatus.NOT_CONNECTED.value,
    ) -> Application:
        self._validate(data)
        if crm_sync_status not in {item.value for item in CrmSyncStatus}:
            raise ApplicationValidationError("invalid_crm_sync_status")

        sessions = SessionRepository(self._session_factory)
        async with self._session_factory() as session:
            try:
                async with session.begin():
                    user = await session.scalar(
                        select(User).where(User.telegram_user_id == telegram_user_id)
                    )
                    if user is None:
                        raise ApplicationValidationError("user_not_found")

                    existing = await session.scalar(
                        select(Application).where(
                            Application.submission_key == data.submission_key
                        )
                    )
                    if existing is not None:
                        if existing.user_id != user.id:
                            raise ApplicationOwnershipError("submission_key_owned_by_other")
                        log_event(
                            logger,
                            action="application.create",
                            stage="idempotent",
                            result="ok",
                            excursion_id=data.excursion_id,
                        )
                        return existing

                    assert data.session_id is not None
                    try:
                        session_row = await sessions.assert_can_book(
                            session_id=data.session_id,
                            participants=data.participants_count,
                            db=session,
                        )
                    except SessionCapacityError as error:
                        raise ApplicationValidationError(error.code) from error
                    except SessionValidationError as error:
                        raise ApplicationValidationError(error.code) from error

                    if session_row.excursion_id != data.excursion_id:
                        raise ApplicationValidationError("session_excursion_mismatch")

                    application = Application(
                        submission_key=data.submission_key,
                        user_id=user.id,
                        excursion_id=data.excursion_id,
                        excursion_title=data.excursion_title,
                        language=data.language,
                        requested_date=data.requested_date,
                        session_id=data.session_id,
                        session_starts_at=data.session_starts_at,
                        participants_count=data.participants_count,
                        customer_name=data.customer_name,
                        phone=data.phone,
                        is_demo=data.is_demo,
                        unit_price=data.unit_price,
                        currency=data.currency,
                        status=ApplicationStatus.NEW.value,
                        crm_sync_status=crm_sync_status,
                    )
                    session.add(application)
                    await session.flush()
                    await session.refresh(application)
                    log_event(
                        logger,
                        action="application.create",
                        stage="insert",
                        result="ok",
                        excursion_id=data.excursion_id,
                        application_id=str(application.id),
                        public_number=application.public_number,
                        session_id=str(data.session_id),
                        crm_sync_status=crm_sync_status,
                    )
                    return application
            except IntegrityError:
                async with self._session_factory() as retry_session:
                    existing = await retry_session.scalar(
                        select(Application).where(
                            Application.submission_key == data.submission_key
                        )
                    )
                    user = await retry_session.scalar(
                        select(User).where(User.telegram_user_id == telegram_user_id)
                    )
                    if existing is None or user is None:
                        raise ApplicationError("create_conflict_unresolved")
                    if existing.user_id != user.id:
                        raise ApplicationOwnershipError("submission_key_owned_by_other")
                    log_event(
                        logger,
                        action="application.create",
                        stage="idempotent_race",
                        result="ok",
                        excursion_id=data.excursion_id,
                    )
                    return existing

    async def get_by_crm_lead_id(self, lead_id: str) -> Application | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(Application).where(Application.crm_lead_id == str(lead_id))
            )

    async def get_by_id(self, application_id: uuid.UUID) -> Application | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(Application).where(Application.id == application_id)
            )

    async def get_for_user(
        self,
        *,
        application_id: uuid.UUID,
        telegram_user_id: int,
    ) -> Application | None:
        async with self._session_factory() as session:
            application = await session.scalar(
                select(Application)
                .join(User, Application.user_id == User.id)
                .where(
                    Application.id == application_id,
                    User.telegram_user_id == telegram_user_id,
                )
            )
            log_event(
                logger,
                action="application.get",
                stage="by_id",
                result="ok" if application else "not_found",
            )
            return application

    async def list_for_user(self, telegram_user_id: int) -> list[Application]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(Application)
                    .join(User, Application.user_id == User.id)
                    .where(User.telegram_user_id == telegram_user_id)
                    .order_by(Application.created_at.desc())
                )
            ).all()
            log_event(
                logger,
                action="application.list",
                stage="by_user",
                result="ok",
                count=len(rows),
            )
            return list(rows)

    async def list_crm_retry_candidates(self, *, limit: int = 20) -> list[Application]:
        statuses = (
            CrmSyncStatus.PENDING.value,
            CrmSyncStatus.ERROR.value,
            CrmSyncStatus.NEEDS_REVIEW.value,
        )
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(Application)
                    .where(
                        Application.crm_sync_status.in_(statuses),
                        Application.crm_lead_id.is_(None),
                    )
                    .order_by(Application.updated_at.asc())
                    .limit(limit)
                )
            ).all()
            return list(rows)

    async def mark_crm_status(
        self,
        *,
        application_id: uuid.UUID,
        status: str,
        lead_id: str | None = None,
        contact_id: str | None = None,
        error_code: str | None = None,
        increment_attempts: bool = False,
        clear_error: bool = False,
    ) -> Application | None:
        if status not in {item.value for item in CrmSyncStatus}:
            raise ApplicationValidationError("invalid_crm_sync_status")
        async with self._session_factory() as session:
            async with session.begin():
                application = await session.scalar(
                    select(Application).where(Application.id == application_id)
                )
                if application is None:
                    return None
                if (
                    application.crm_lead_id
                    and application.crm_sync_status == CrmSyncStatus.SENT.value
                    and status != CrmSyncStatus.SENT.value
                ):
                    return application
                application.crm_sync_status = status
                if lead_id is not None:
                    application.crm_lead_id = lead_id
                if contact_id is not None:
                    application.crm_contact_id = contact_id
                if increment_attempts:
                    application.crm_sync_attempts = int(application.crm_sync_attempts) + 1
                if clear_error:
                    application.crm_last_error_code = None
                elif error_code is not None:
                    application.crm_last_error_code = error_code[:64]
                if status == CrmSyncStatus.SENT.value:
                    application.crm_synced_at = datetime.now(timezone.utc)
                    application.crm_last_error_code = None
                await session.flush()
                log_event(
                    logger,
                    action="application.crm",
                    stage="status",
                    result=status,
                    application_id=str(application_id),
                    attempts=application.crm_sync_attempts,
                    error_code=application.crm_last_error_code or "-",
                )
                return application
