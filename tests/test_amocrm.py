"""Mock-тесты интеграции заявок с amoCRM (без реального API)."""

from __future__ import annotations

import asyncio
import json
import tempfile
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from excursion_ai.config import Settings
from excursion_ai.crm.client import (
    AmoCrmApiError,
    AmoCrmClient,
    AmoCrmUncertainError,
)
from excursion_ai.crm.fields import PARTICIPANTS_FIELD_NAME, resolve_participants_field_id
from excursion_ai.crm.payload import (
    build_complex_lead_payload,
    deal_budget,
    deal_name_for,
)
from excursion_ai.crm.pipelines import find_pipeline_target
from excursion_ai.crm.sync import ApplicationCrmSyncService
from excursion_ai.crm.tokens import OAuthTokens, TokenStore
from excursion_ai.db.models import Application, CrmSyncStatus
from excursion_ai.db.public_number import format_public_number

_PIPELINES = [
    {
        "id": 111,
        "name": "Покупка",
        "_embedded": {
            "statuses": [
                {"id": 1, "name": "Неразобранное"},
                {"id": 2, "name": "Получено новое обращение"},
            ]
        },
    },
    {
        "id": 222,
        "name": "ЭКСКУРСИИ",
        "_embedded": {
            "statuses": [
                {"id": 301, "name": "Новая заявка"},
                {"id": 302, "name": "В работе"},
            ]
        },
    },
]

_CUSTOM_FIELDS = [
    {"id": 101, "name": "Application ID"},
    {"id": 103, "name": PARTICIPANTS_FIELD_NAME},
    {"id": 104, "name": "Экскурсия"},
    {"id": 102, "name": "Дата"},
]


def _settings(**overrides: object) -> Settings:
    base = dict(
        bot_token="123456:TESTTOKEN_FOR_UNIT_TESTS_ONLY",
        amocrm_enabled=True,
        amocrm_subdomain="testsub",
        amocrm_client_id="client-id",
        amocrm_client_secret="client-secret-value",
        amocrm_redirect_uri="https://example.com/oauth",
        amocrm_access_token="access-token-value",
        amocrm_refresh_token="refresh-token-value",
        amocrm_token_store_path=str(Path(tempfile.gettempdir()) / "ea_amocrm_tokens.json"),
        amocrm_pipeline_id=None,
        amocrm_status_id=None,
        amocrm_pipeline_name="ЭКСКУРСИИ",
        amocrm_status_name="Новая заявка",
        amocrm_cf_application_id=101,
        amocrm_cf_excursion=104,
        amocrm_cf_requested_date=102,
        amocrm_cf_participants=103,
        amocrm_timeout_seconds=5.0,
        amocrm_max_attempts=5,
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _application(
    *,
    phone: str | None = "+391234567890",
    public_number: int = 1,
    participants_count: int = 3,
) -> Application:
    app_id = uuid.uuid4()
    return Application(
        id=app_id,
        public_number=public_number,
        submission_key=f"sub-{app_id.hex[:8]}",
        user_id=1,
        excursion_id="ravenna-mosaics",
        excursion_title="Равенна — город золотых мозаик",
        language="ru",
        requested_date=date(2026, 10, 15),
        participants_count=participants_count,
        customer_name="Test Visitor",
        phone=phone,
        is_demo=True,
        unit_price=Decimal("50.00"),
        currency="EUR",
        status="new",
        crm_sync_status=CrmSyncStatus.PENDING.value,
        crm_contact_id=None,
        crm_lead_id=None,
        crm_synced_at=None,
        crm_sync_attempts=0,
        crm_last_error_code=None,
    )


def _clone_app(application: Application, **overrides: object) -> Application:
    data = {
        "id": application.id,
        "public_number": application.public_number,
        "submission_key": application.submission_key,
        "user_id": 1,
        "excursion_id": application.excursion_id,
        "excursion_title": application.excursion_title,
        "language": "ru",
        "requested_date": application.requested_date,
        "participants_count": application.participants_count,
        "customer_name": application.customer_name,
        "phone": application.phone,
        "is_demo": True,
        "unit_price": application.unit_price,
        "currency": "EUR",
        "status": "new",
        "crm_sync_status": application.crm_sync_status,
        "crm_contact_id": application.crm_contact_id,
        "crm_lead_id": application.crm_lead_id,
        "crm_synced_at": None,
        "crm_sync_attempts": application.crm_sync_attempts,
        "crm_last_error_code": application.crm_last_error_code,
    }
    data.update(overrides)
    return Application(**data)  # type: ignore[arg-type]


def _mock_client_with_pipelines(**kwargs: object) -> AsyncMock:
    client = AsyncMock()
    client.list_pipelines = AsyncMock(return_value=_PIPELINES)
    client.list_lead_custom_fields = AsyncMock(return_value=_CUSTOM_FIELDS)
    client.create_lead_custom_field = AsyncMock(
        return_value={"id": 103, "name": PARTICIPANTS_FIELD_NAME, "type": "numeric"}
    )
    client.find_lead_by_application_field = AsyncMock(return_value=None)
    client.find_lead_by_query = AsyncMock(return_value=None)
    client.get_lead = AsyncMock(return_value=None)
    client.update_lead = AsyncMock(return_value=None)
    for key, value in kwargs.items():
        setattr(client, key, value)
    return client


def test_payload_requires_pipeline_and_maps_fields() -> None:
    settings = _settings()
    with_phone = _application(phone="+391111111111", public_number=1)
    payload = build_complex_lead_payload(
        with_phone,
        settings,
        pipeline_id=222,
        status_id=301,
        participants_field_id=103,
    )
    assert payload["name"] == deal_name_for(with_phone)
    assert payload["name"] == "ExcursionAI #0001 — Равенна — город золотых мозаик"
    assert str(with_phone.id) not in payload["name"]
    assert payload["price"] == deal_budget(with_phone) == 150
    assert payload["pipeline_id"] == 222
    assert payload["status_id"] == 301
    assert payload["request_id"] == str(with_phone.id)
    contact = payload["_embedded"]["contacts"][0]
    assert contact["name"] == "Test Visitor"
    assert contact["custom_fields_values"][0]["field_code"] == "PHONE"
    by_id = {
        item["field_id"]: item["values"][0]["value"]
        for item in payload["custom_fields_values"]
    }
    assert by_id[101] == str(with_phone.id)
    assert by_id[104] == "Равенна — город золотых мозаик"
    assert by_id[102] == "2026-10-15"
    assert by_id[103] == 3
    assert isinstance(by_id[103], int)

    wide = _application(public_number=10001)
    assert deal_name_for(wide).startswith("ExcursionAI #10001 —")

    no_phone = _application(phone=None)
    payload2 = build_complex_lead_payload(
        no_phone,
        settings,
        pipeline_id=222,
        status_id=301,
        participants_field_id=103,
    )
    assert "custom_fields_values" not in payload2["_embedded"]["contacts"][0]


def test_resolve_pipeline_by_name_and_ids() -> None:
    settings = _settings(
        amocrm_pipeline_name="экскурсии",
        amocrm_status_name="новая заявка",
    )
    target = find_pipeline_target(_PIPELINES, settings)
    assert target.pipeline_id == 222
    assert target.status_id == 301

    by_ids = _settings(
        amocrm_pipeline_name="",
        amocrm_status_name="",
        amocrm_pipeline_id=222,
        amocrm_status_id=301,
    )
    target2 = find_pipeline_target(_PIPELINES, by_ids)
    assert target2.pipeline_id == 222
    assert target2.status_id == 301

    missing = _settings(
        amocrm_pipeline_name="Нет такой",
        amocrm_status_name="Новая заявка",
        amocrm_pipeline_id=None,
        amocrm_status_id=None,
    )
    try:
        find_pipeline_target(_PIPELINES, missing)
        raise AssertionError("expected pipeline_not_found")
    except AmoCrmApiError as error:
        assert error.code == "pipeline_not_found"

    bad_status = _settings(
        amocrm_pipeline_name="ЭКСКУРСИИ",
        amocrm_status_name="Несуществующий этап",
    )
    try:
        find_pipeline_target(_PIPELINES, bad_status)
        raise AssertionError("expected status_not_found")
    except AmoCrmApiError as error:
        assert error.code == "status_not_found"

    no_config = _settings(
        amocrm_pipeline_name="",
        amocrm_status_name="",
        amocrm_pipeline_id=None,
        amocrm_status_id=None,
    )
    try:
        find_pipeline_target(_PIPELINES, no_config)
        raise AssertionError("expected pipeline_config_missing")
    except AmoCrmApiError as error:
        assert error.code == "pipeline_config_missing"


def test_token_store_atomic_refresh() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "tokens.json"
        store = TokenStore(path)
        store.save(OAuthTokens(access_token="old-access-xx", refresh_token="old-refresh-xx"))
        loaded = store.load()
        assert loaded is not None
        assert loaded.access_token == "old-access-xx"
        store.save(
            OAuthTokens(access_token="new-access-yy", refresh_token="new-refresh-yy")
        )
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["access_token"] == "new-access-yy"
        assert raw["refresh_token"] == "new-refresh-yy"


async def _test_oauth_refresh_with_mock() -> None:
    settings = _settings()
    http = AsyncMock()
    response = MagicMock()
    response.status_code = 200
    response.json = MagicMock(
        return_value={
            "access_token": "refreshed-access-token-zz",
            "refresh_token": "refreshed-refresh-token-zz",
        }
    )
    http.post = AsyncMock(return_value=response)
    with tempfile.TemporaryDirectory() as tmp:
        store = TokenStore(Path(tmp) / "t.json")
        store.save(
            OAuthTokens(
                access_token="access-token-value",
                refresh_token="refresh-token-value",
            )
        )
        client = AmoCrmClient(settings, token_store=store, http_client=http)
        tokens = await client.refresh_access_token()
        assert tokens.access_token == "refreshed-access-token-zz"
        assert tokens.refresh_token == "refreshed-refresh-token-zz"
        await client.aclose()


async def _test_resolve_participants_field_by_name() -> None:
    client = AsyncMock()
    client.list_lead_custom_fields = AsyncMock(return_value=_CUSTOM_FIELDS)
    client.create_lead_custom_field = AsyncMock()
    field_id = await resolve_participants_field_id(client, _settings())
    assert field_id == 103
    client.create_lead_custom_field.assert_not_awaited()

    client.list_lead_custom_fields = AsyncMock(
        return_value=[{"id": 1, "name": "Другое"}]
    )
    client.create_lead_custom_field = AsyncMock(
        return_value={"id": 777, "name": PARTICIPANTS_FIELD_NAME, "type": "numeric"}
    )
    created_id = await resolve_participants_field_id(client, _settings())
    assert created_id == 777
    client.create_lead_custom_field.assert_awaited_once()


async def _test_successful_sync_uses_excursions_pipeline() -> None:
    settings = _settings()
    application = _application(phone=None)
    sent = _clone_app(
        application,
        crm_sync_status=CrmSyncStatus.SENT.value,
        crm_contact_id="c-9",
        crm_lead_id="lead-77",
        crm_sync_attempts=1,
        crm_last_error_code=None,
    )
    apps = AsyncMock()
    apps.mark_crm_status = AsyncMock(return_value=sent)
    apps.get_by_id = AsyncMock(side_effect=[application, application, sent, sent])

    client = _mock_client_with_pipelines(
        create_lead_complex=AsyncMock(return_value=("lead-77", "c-9"))
    )
    service = ApplicationCrmSyncService(settings, apps, client=client)
    result = await service.sync_application(application.id)
    assert result is not None
    assert result.crm_lead_id == "lead-77"
    client.create_lead_complex.assert_awaited_once()
    payload = client.create_lead_complex.await_args.args[0]
    assert payload["pipeline_id"] == 222
    assert payload["status_id"] == 301
    assert payload["pipeline_id"] != 111
    by_id = {
        item["field_id"]: item["values"][0]["value"]
        for item in payload["custom_fields_values"]
    }
    assert by_id[103] == 3

    result2 = await service.sync_application(application.id)
    assert result2 is not None
    assert client.create_lead_complex.await_count == 1


async def _test_missing_participants_field_is_created_then_sync() -> None:
    settings = _settings()
    application = _application()
    sent = _clone_app(
        application,
        crm_sync_status=CrmSyncStatus.SENT.value,
        crm_lead_id="lead-88",
        crm_contact_id="c-88",
        crm_sync_attempts=1,
        crm_last_error_code=None,
    )
    apps = AsyncMock()
    apps.get_by_id = AsyncMock(side_effect=[application, application, sent])
    apps.mark_crm_status = AsyncMock(side_effect=[application, sent])
    client = _mock_client_with_pipelines(
        list_lead_custom_fields=AsyncMock(return_value=[{"id": 9, "name": "Прочее"}]),
        create_lead_custom_field=AsyncMock(
            return_value={"id": 103, "name": PARTICIPANTS_FIELD_NAME, "type": "numeric"}
        ),
        create_lead_complex=AsyncMock(return_value=("lead-88", "c-88")),
    )
    service = ApplicationCrmSyncService(settings, apps, client=client)
    result = await service.sync_application(application.id)
    assert result is not None
    assert result.crm_sync_status == CrmSyncStatus.SENT.value
    client.create_lead_custom_field.assert_awaited_once()
    client.create_lead_complex.assert_awaited_once()
    payload = client.create_lead_complex.await_args.args[0]
    by_id = {
        item["field_id"]: item["values"][0]["value"]
        for item in payload["custom_fields_values"]
    }
    assert by_id[103] == 3


async def _test_participants_create_failure_blocks_sync() -> None:
    settings = _settings()
    application = _application()
    errored = _clone_app(
        application,
        crm_sync_status=CrmSyncStatus.ERROR.value,
        crm_sync_attempts=1,
        crm_last_error_code="participants_field_not_found",
    )
    apps = AsyncMock()
    apps.get_by_id = AsyncMock(side_effect=[application, application, errored])
    apps.mark_crm_status = AsyncMock(side_effect=[application, errored])
    client = _mock_client_with_pipelines(
        list_lead_custom_fields=AsyncMock(
            side_effect=[
                [{"id": 9, "name": "Прочее"}],
                [{"id": 9, "name": "Прочее"}],
            ]
        ),
        create_lead_custom_field=AsyncMock(
            side_effect=AmoCrmApiError(
                "lead_custom_field_create_http_400", status_code=400
            )
        ),
        create_lead_complex=AsyncMock(),
    )
    service = ApplicationCrmSyncService(settings, apps, client=client)
    result = await service.sync_application(application.id)
    assert result is not None
    assert result.crm_sync_status == CrmSyncStatus.ERROR.value
    assert result.crm_last_error_code == "participants_field_not_found"
    client.create_lead_complex.assert_not_awaited()


async def _test_resync_updates_existing_lead_without_duplicate() -> None:
    settings = _settings()
    application = _application(public_number=7, participants_count=4)
    existing = {
        "id": 555,
        "name": deal_name_for(application),
        "_embedded": {"contacts": [{"id": 88}]},
    }
    sent = _clone_app(
        application,
        crm_sync_status=CrmSyncStatus.SENT.value,
        crm_lead_id="555",
        crm_contact_id="88",
        crm_sync_attempts=1,
    )
    apps = AsyncMock()
    apps.get_by_id = AsyncMock(side_effect=[application, application, sent])
    apps.mark_crm_status = AsyncMock(side_effect=[application, sent])
    client = _mock_client_with_pipelines(
        find_lead_by_query=AsyncMock(return_value=existing),
        create_lead_complex=AsyncMock(),
    )
    service = ApplicationCrmSyncService(settings, apps, client=client)
    result = await service.sync_application(application.id)
    assert result is not None
    assert result.crm_lead_id == "555"
    client.create_lead_complex.assert_not_awaited()
    client.update_lead.assert_awaited_once()
    update = client.update_lead.await_args.args[0]
    assert update["id"] == 555
    assert update["name"] == f"ExcursionAI #{format_public_number(7)} — {application.excursion_title}"
    by_id = {
        item["field_id"]: item["values"][0]["value"]
        for item in update["custom_fields_values"]
    }
    assert by_id[103] == 4


async def _test_missing_pipeline_does_not_create_lead() -> None:
    settings = _settings(
        amocrm_pipeline_name="Другая воронка",
        amocrm_status_name="Новая заявка",
    )
    application = _application()
    errored = _clone_app(
        application,
        crm_sync_status=CrmSyncStatus.ERROR.value,
        crm_sync_attempts=1,
        crm_last_error_code="pipeline_not_found",
    )
    apps = AsyncMock()
    apps.get_by_id = AsyncMock(side_effect=[application, application, errored])
    apps.mark_crm_status = AsyncMock(side_effect=[application, errored])
    client = _mock_client_with_pipelines(create_lead_complex=AsyncMock())
    service = ApplicationCrmSyncService(settings, apps, client=client)
    result = await service.sync_application(application.id)
    assert result is not None
    assert result.crm_sync_status == CrmSyncStatus.ERROR.value
    assert result.crm_last_error_code == "pipeline_not_found"
    client.create_lead_complex.assert_not_awaited()


async def _test_crm_error_then_retry() -> None:
    settings = _settings()
    application = _application()
    after_error = _clone_app(
        application,
        crm_sync_status=CrmSyncStatus.ERROR.value,
        crm_sync_attempts=1,
        crm_last_error_code="leads_create_http_500",
    )
    sent = _clone_app(
        application,
        crm_sync_status=CrmSyncStatus.SENT.value,
        crm_contact_id="c-1",
        crm_lead_id="lead-1",
        crm_sync_attempts=2,
        crm_last_error_code=None,
    )
    apps = AsyncMock()
    apps.get_by_id = AsyncMock(
        side_effect=[
            application,
            application,
            after_error,
            after_error,
            after_error,
            sent,
        ]
    )
    apps.mark_crm_status = AsyncMock(
        side_effect=[application, after_error, after_error, sent]
    )
    client = _mock_client_with_pipelines(
        create_lead_complex=AsyncMock(
            side_effect=[
                AmoCrmApiError("leads_create_http_500", status_code=500),
                ("lead-1", "c-1"),
            ]
        )
    )
    service = ApplicationCrmSyncService(settings, apps, client=client)
    failed = await service.sync_application(application.id)
    assert failed is not None
    assert failed.crm_sync_status == CrmSyncStatus.ERROR.value
    ok = await service.sync_application(application.id)
    assert ok is not None
    assert ok.crm_lead_id == "lead-1"
    assert client.create_lead_complex.await_count == 2


async def _test_timeout_uncertain_marks_needs_review() -> None:
    settings = _settings(amocrm_cf_application_id=None)
    application = _application()
    needs = _clone_app(
        application,
        crm_sync_status=CrmSyncStatus.NEEDS_REVIEW.value,
        crm_sync_attempts=1,
        crm_last_error_code="request_timeout_uncertain",
    )
    apps = AsyncMock()
    apps.get_by_id = AsyncMock(side_effect=[application, application, needs])
    apps.mark_crm_status = AsyncMock(side_effect=[application, needs])
    client = _mock_client_with_pipelines(
        create_lead_complex=AsyncMock(
            side_effect=AmoCrmUncertainError("request_timeout_uncertain")
        )
    )
    service = ApplicationCrmSyncService(settings, apps, client=client)
    result = await service.sync_application(application.id)
    assert result is not None
    assert result.crm_sync_status == CrmSyncStatus.NEEDS_REVIEW.value

    recovered_app = _clone_app(
        application,
        crm_sync_status=CrmSyncStatus.SENT.value,
        crm_lead_id="900001",
        crm_sync_attempts=2,
        crm_last_error_code=None,
    )
    apps.get_by_id = AsyncMock(side_effect=[needs, needs, recovered_app])
    apps.mark_crm_status = AsyncMock(side_effect=[needs, recovered_app])
    client.find_lead_by_query = AsyncMock(
        return_value={"id": 900001, "name": deal_name_for(application)}
    )
    client.create_lead_complex.reset_mock()
    client.update_lead.reset_mock()
    recovered = await service.sync_application(application.id)
    assert recovered is not None
    assert recovered.crm_lead_id == "900001"
    client.create_lead_complex.assert_not_awaited()
    client.update_lead.assert_awaited_once()
    update = client.update_lead.await_args.args[0]
    assert update["id"] == 900001
    by_id = {
        item["field_id"]: item["values"][0]["value"]
        for item in update["custom_fields_values"]
    }
    assert by_id[103] == application.participants_count


async def _test_migration_columns_exist() -> None:
    from sqlalchemy import text

    from excursion_ai.config import load_settings
    from excursion_ai.db.session import dispose_engine, init_engine

    settings = load_settings(require_db=True)
    factory = init_engine(settings)
    try:
        async with factory() as session:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name = 'applications'
                          AND column_name IN (
                            'crm_synced_at',
                            'crm_sync_attempts',
                            'crm_last_error_code',
                            'public_number'
                          )
                        """
                    )
                )
            ).scalars().all()
        assert set(rows) >= {
            "crm_synced_at",
            "crm_sync_attempts",
            "crm_last_error_code",
            "public_number",
        }
    finally:
        await dispose_engine()


if __name__ == "__main__":
    test_payload_requires_pipeline_and_maps_fields()
    print("OK payload + pipeline fields")
    test_resolve_pipeline_by_name_and_ids()
    print("OK pipeline resolve / no fallback")
    test_token_store_atomic_refresh()
    print("OK token store")
    asyncio.run(_test_oauth_refresh_with_mock())
    print("OK oauth refresh mock")
    asyncio.run(_test_resolve_participants_field_by_name())
    print("OK participants field by name")
    asyncio.run(_test_successful_sync_uses_excursions_pipeline())
    print("OK sync to EXCURSIONS pipeline")
    asyncio.run(_test_missing_participants_field_is_created_then_sync())
    print("OK missing participants field is created")
    asyncio.run(_test_participants_create_failure_blocks_sync())
    print("OK participants create failure blocks sync")
    asyncio.run(_test_resync_updates_existing_lead_without_duplicate())
    print("OK resync updates existing lead")
    asyncio.run(_test_missing_pipeline_does_not_create_lead())
    print("OK missing pipeline blocks create")
    asyncio.run(_test_crm_error_then_retry())
    print("OK crm error + retry")
    asyncio.run(_test_timeout_uncertain_marks_needs_review())
    print("OK timeout uncertain + reconcile")
    asyncio.run(_test_migration_columns_exist())
    print("OK crm migration columns")
    print("OK amocrm suite")
