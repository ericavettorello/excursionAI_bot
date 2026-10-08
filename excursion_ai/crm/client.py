"""HTTP-клиент amoCRM API v4 + OAuth 2.0 refresh."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from excursion_ai.config import Settings
from excursion_ai.crm.tokens import OAuthTokens, TokenStore
from excursion_ai.log_context import log_event

logger = logging.getLogger(__name__)


class AmoCrmError(Exception):
    def __init__(self, code: str, *, status_code: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class AmoCrmAuthError(AmoCrmError):
    pass


class AmoCrmApiError(AmoCrmError):
    pass


class AmoCrmUncertainError(AmoCrmError):
    """Запрос мог быть принят CRM, ответ не получен (таймаут после отправки)."""


class AmoCrmClient:
    def __init__(
        self,
        settings: Settings,
        *,
        token_store: TokenStore | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._token_store = token_store or TokenStore(settings.amocrm_token_store_path)
        fallback = OAuthTokens(
            access_token=settings.amocrm_access_token,
            refresh_token=settings.amocrm_refresh_token,
        )
        self._tokens = self._token_store.load(fallback=fallback) or fallback
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            timeout=settings.amocrm_timeout_seconds
        )

    @property
    def base_url(self) -> str:
        return self._settings.amocrm_base_url()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def refresh_access_token(self) -> OAuthTokens:
        refresh = self._tokens.refresh_token
        if not refresh:
            raise AmoCrmAuthError("refresh_token_missing")
        url = f"{self.base_url}/oauth2/access_token"
        body = {
            "client_id": self._settings.amocrm_client_id,
            "client_secret": self._settings.amocrm_client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "redirect_uri": self._settings.amocrm_redirect_uri,
        }
        try:
            response = await self._http.post(url, json=body)
        except httpx.TimeoutException as error:
            raise AmoCrmApiError("oauth_timeout") from error
        except httpx.HTTPError as error:
            raise AmoCrmApiError("oauth_network_error") from error
        if response.status_code >= 400:
            log_event(
                logger,
                action="crm.oauth",
                stage="refresh",
                result="error",
                level=logging.ERROR,
                status_code=response.status_code,
            )
            raise AmoCrmAuthError("oauth_refresh_failed", status_code=response.status_code)
        data = response.json()
        access = str(data.get("access_token") or "").strip()
        new_refresh = str(data.get("refresh_token") or "").strip()
        if not access or not new_refresh:
            raise AmoCrmAuthError("oauth_refresh_incomplete")
        tokens = OAuthTokens(access_token=access, refresh_token=new_refresh)
        self._token_store.save(tokens)
        self._tokens = tokens
        log_event(
            logger,
            action="crm.oauth",
            stage="refresh",
            result="ok",
        )
        return tokens

    async def _authorized_request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        params: dict[str, Any] | None = None,
        allow_refresh: bool = True,
    ) -> httpx.Response:
        if not self._tokens.access_token and self._tokens.refresh_token:
            await self.refresh_access_token()
        headers = {
            "Authorization": f"Bearer {self._tokens.access_token}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}{path}"
        try:
            response = await self._http.request(
                method,
                url,
                headers=headers,
                json=json_body,
                params=params,
            )
        except httpx.TimeoutException as error:
            # Таймаут после ухода запроса считаем неопределённым для мутаций.
            if method.upper() in {"POST", "PATCH", "PUT"} and json_body is not None:
                raise AmoCrmUncertainError("request_timeout_uncertain") from error
            raise AmoCrmApiError("request_timeout") from error
        except httpx.TransportError as error:
            if method.upper() in {"POST", "PATCH", "PUT"} and json_body is not None:
                raise AmoCrmUncertainError("transport_uncertain") from error
            raise AmoCrmApiError("network_error") from error

        if response.status_code == 401 and allow_refresh:
            await self.refresh_access_token()
            return await self._authorized_request(
                method,
                path,
                json_body=json_body,
                params=params,
                allow_refresh=False,
            )
        return response

    async def list_pipelines(self) -> list[dict[str, Any]]:
        response = await self._authorized_request("GET", "/api/v4/leads/pipelines")
        if response.status_code == 204:
            return []
        if response.status_code >= 400:
            raise AmoCrmApiError(
                f"pipelines_http_{response.status_code}",
                status_code=response.status_code,
            )
        payload = response.json()
        embedded = payload.get("_embedded") or {}
        pipelines = embedded.get("pipelines") or []
        return list(pipelines)

    async def list_lead_custom_fields(self) -> list[dict[str, Any]]:
        """Все пользовательские поля сделки (с пагинацией)."""
        page = 1
        collected: list[dict[str, Any]] = []
        while True:
            response = await self._authorized_request(
                "GET",
                "/api/v4/leads/custom_fields",
                params={"limit": 50, "page": page},
            )
            if response.status_code == 204:
                break
            if response.status_code >= 400:
                raise AmoCrmApiError(
                    f"lead_custom_fields_http_{response.status_code}",
                    status_code=response.status_code,
                )
            payload = response.json()
            embedded = payload.get("_embedded") or {}
            fields = embedded.get("custom_fields") or []
            if not fields:
                break
            collected.extend(fields)
            links = payload.get("_links") or {}
            if not links.get("next"):
                break
            page += 1
            if page > 20:
                break
        return collected

    async def create_lead_custom_field(
        self,
        *,
        name: str,
        field_type: str = "numeric",
    ) -> dict[str, Any]:
        """Создаёт пользовательское поле сделки. Возвращает объект поля с id."""
        response = await self._authorized_request(
            "POST",
            "/api/v4/leads/custom_fields",
            json_body=[{"name": name, "type": field_type}],
        )
        if response.status_code >= 400:
            raise AmoCrmApiError(
                f"lead_custom_field_create_http_{response.status_code}",
                status_code=response.status_code,
            )
        data = response.json()
        embedded = data.get("_embedded") or {}
        fields = embedded.get("custom_fields") or []
        if not fields:
            # Некоторые ответы отдают массив верхнего уровня.
            if isinstance(data, list) and data:
                fields = data
        if not fields:
            raise AmoCrmApiError("lead_custom_field_create_empty")
        created = fields[0]
        if created.get("id") is None:
            raise AmoCrmApiError("lead_custom_field_create_missing_id")
        return created

    async def get_lead(self, lead_id: str | int) -> dict[str, Any] | None:
        response = await self._authorized_request(
            "GET",
            f"/api/v4/leads/{int(lead_id)}",
            params={"with": "contacts"},
        )
        if response.status_code == 204:
            return None
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise AmoCrmApiError(
                f"leads_get_http_{response.status_code}",
                status_code=response.status_code,
            )
        return response.json()

    async def update_lead(self, lead_payload: dict[str, Any]) -> None:
        """PATCH /api/v4/leads — обновление существующей сделки (имя, price, CF)."""
        response = await self._authorized_request(
            "PATCH",
            "/api/v4/leads",
            json_body=[lead_payload],
        )
        if response.status_code >= 400:
            raise AmoCrmApiError(
                f"leads_update_http_{response.status_code}",
                status_code=response.status_code,
            )

    async def find_lead_by_query(self, query: str) -> dict[str, Any] | None:
        response = await self._authorized_request(
            "GET",
            "/api/v4/leads",
            params={"query": query, "limit": 5},
        )
        if response.status_code == 204:
            return None
        if response.status_code >= 400:
            raise AmoCrmApiError(
                f"leads_search_http_{response.status_code}",
                status_code=response.status_code,
            )
        payload = response.json()
        embedded = payload.get("_embedded") or {}
        leads = embedded.get("leads") or []
        if not leads:
            return None
        return leads[0]

    async def find_lead_by_application_field(
        self,
        *,
        field_id: int,
        application_id: str,
    ) -> dict[str, Any] | None:
        # Фильтр по значению доп. поля (если поле настроено в аккаунте).
        params = {
            f"filter[custom_fields_values][{field_id}][]": application_id,
            "limit": 5,
        }
        response = await self._authorized_request(
            "GET",
            "/api/v4/leads",
            params=params,
        )
        if response.status_code == 204:
            return None
        if response.status_code >= 400:
            raise AmoCrmApiError(
                f"leads_filter_http_{response.status_code}",
                status_code=response.status_code,
            )
        payload = response.json()
        embedded = payload.get("_embedded") or {}
        leads = embedded.get("leads") or []
        return leads[0] if leads else None

    async def create_lead_complex(
        self, lead_payload: dict[str, Any]
    ) -> tuple[str, str | None]:
        """Создаёт сделку с контактом. Возвращает (lead_id, contact_id|None)."""
        response = await self._authorized_request(
            "POST",
            "/api/v4/leads/complex",
            json_body=[lead_payload],
        )
        if response.status_code >= 400:
            raise AmoCrmApiError(
                f"leads_create_http_{response.status_code}",
                status_code=response.status_code,
            )
        data = response.json()
        if not isinstance(data, list) or not data:
            raise AmoCrmApiError("leads_create_empty_response")
        item = data[0]
        lead_id = item.get("id")
        if lead_id is None:
            raise AmoCrmApiError("leads_create_missing_id")
        contact_id = None
        embedded = item.get("_embedded") or {}
        contacts = embedded.get("contacts") or []
        if contacts and contacts[0].get("id") is not None:
            contact_id = str(contacts[0]["id"])
        return str(lead_id), contact_id

    async def add_lead_note(self, lead_id: str | int, text: str) -> None:
        response = await self._authorized_request(
            "POST",
            "/api/v4/leads/notes",
            json_body=[
                {
                    "entity_id": int(lead_id),
                    "note_type": "common",
                    "params": {"text": text},
                }
            ],
        )
        if response.status_code >= 400:
            raise AmoCrmApiError(
                f"leads_note_http_{response.status_code}",
                status_code=response.status_code,
            )
