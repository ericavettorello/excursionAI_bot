from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import URL

from excursion_ai.log_context import register_secret_value

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCALES_DIR = PROJECT_ROOT / "locales"
CATALOG_PATH = PROJECT_ROOT / "data" / "excursions.json"
LOGS_DIR = PROJECT_ROOT / "logs"
SUPPORTED_LANGS = ("ru", "en", "it")


class ConfigError(Exception):
    """Ошибка конфигурации без раскрытия секретов."""


@dataclass(frozen=True)
class Settings:
    bot_token: str
    log_level: str = "INFO"
    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_name: str = "excursion_ai"
    db_user: str = ""
    db_password: str = ""
    openai_api_key: str = ""
    openai_model: str = ""
    ai_enabled: bool = False
    ai_daily_user_limit: int = 20
    ai_daily_global_limit: int = 200
    ai_admin_translation_daily_limit: int = 50
    ai_max_input_chars: int = 2000
    ai_max_output_tokens: int = 800
    ai_timeout_seconds: float = 30.0
    ai_max_retries: int = 1
    admin_ids: tuple[int, ...] = ()
    catalog_cache_ttl_seconds: int = 60
    amocrm_enabled: bool = False
    amocrm_subdomain: str = ""
    amocrm_client_id: str = ""
    amocrm_client_secret: str = ""
    amocrm_redirect_uri: str = ""
    amocrm_access_token: str = ""
    amocrm_refresh_token: str = ""
    amocrm_token_store_path: str = ""
    amocrm_pipeline_id: int | None = None
    amocrm_status_id: int | None = None
    amocrm_pipeline_name: str = ""
    amocrm_status_name: str = ""
    amocrm_cf_application_id: int | None = None
    amocrm_cf_excursion: int | None = None
    amocrm_cf_requested_date: int | None = None
    amocrm_cf_participants: int | None = None
    amocrm_cf_public_number: int | None = None
    amocrm_cf_session_date: int | None = None
    amocrm_cf_session_time: int | None = None
    amocrm_status_name_confirmed: str = "Бронь подтверждена"
    amocrm_status_name_cancelled: str = "Бронь отменена"
    amocrm_booking_pipeline_name: str = ""
    amocrm_timeout_seconds: float = 30.0
    amocrm_max_attempts: int = 5
    amocrm_webhook_enabled: bool = False
    amocrm_webhook_host: str = "0.0.0.0"
    amocrm_webhook_port: int = 8080
    amocrm_webhook_path: str = "/amocrm/webhook"
    amocrm_webhook_secret: str = ""

    def is_admin(self, telegram_user_id: int) -> bool:
        return bool(self.admin_ids) and telegram_user_id in self.admin_ids

    def database_url(self) -> URL:
        if not self.db_user:
            raise ConfigError(
                "Не задан DB_USER. Укажите роль приложения PostgreSQL в .env "
                "(пример полей есть в .env.example)."
            )
        if not self.db_password:
            raise ConfigError(
                "Не задан DB_PASSWORD. Укажите пароль роли приложения в .env. "
                "Пароль не угадывается и не записывается в код."
            )
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )

    def database_url_sync(self) -> URL:
        url = self.database_url()
        return url.set(drivername="postgresql+psycopg")

    def ai_ready(self) -> bool:
        return bool(self.ai_enabled and self.openai_api_key and self.openai_model)

    def ai_missing_settings(self) -> list[str]:
        if not self.ai_enabled:
            return ["AI_ENABLED"]
        missing: list[str] = []
        if not self.openai_api_key:
            missing.append("OPENAI_API_KEY")
        if not self.openai_model:
            missing.append("OPENAI_MODEL")
        return missing

    def amocrm_ready(self) -> bool:
        if not (
            self.amocrm_enabled
            and self.amocrm_subdomain
            and self.amocrm_client_id
            and self.amocrm_client_secret
            and self.amocrm_redirect_uri
            and (self.amocrm_access_token or self.amocrm_refresh_token)
        ):
            return False
        has_ids = (
            self.amocrm_pipeline_id is not None and self.amocrm_status_id is not None
        )
        has_names = bool(
            self.amocrm_pipeline_name.strip() and self.amocrm_status_name.strip()
        )
        return has_ids or has_names

    def amocrm_missing_settings(self) -> list[str]:
        if not self.amocrm_enabled:
            return ["AMOCRM_ENABLED"]
        missing: list[str] = []
        for field, attr in (
            ("AMOCRM_SUBDOMAIN", "amocrm_subdomain"),
            ("AMOCRM_CLIENT_ID", "amocrm_client_id"),
            ("AMOCRM_CLIENT_SECRET", "amocrm_client_secret"),
            ("AMOCRM_REDIRECT_URI", "amocrm_redirect_uri"),
        ):
            if not getattr(self, attr):
                missing.append(field)
        if not self.amocrm_access_token and not self.amocrm_refresh_token:
            missing.append("AMOCRM_ACCESS_TOKEN/AMOCRM_REFRESH_TOKEN")
        has_ids = (
            self.amocrm_pipeline_id is not None and self.amocrm_status_id is not None
        )
        has_names = bool(
            self.amocrm_pipeline_name.strip() and self.amocrm_status_name.strip()
        )
        if not has_ids and not has_names:
            missing.append(
                "AMOCRM_PIPELINE_ID+AMOCRM_STATUS_ID "
                "или AMOCRM_PIPELINE_NAME+AMOCRM_STATUS_NAME"
            )
        return missing

    def amocrm_base_url(self) -> str:
        return f"https://{self.amocrm_subdomain}.amocrm.ru"


def _load_env() -> None:
    load_dotenv(PROJECT_ROOT / ".env")


def _parse_bool(raw: str, *, field: str) -> bool:
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off", ""}:
        return False
    raise ConfigError(f"{field} должен быть true/false.")


def _parse_positive_int(raw: str, *, field: str, default: int) -> int:
    text = raw.strip()
    if not text:
        return default
    try:
        value = int(text)
    except ValueError as error:
        raise ConfigError(f"{field} должен быть целым числом.") from error
    if value <= 0:
        raise ConfigError(f"{field} должен быть больше 0.")
    return value


def _parse_positive_float(raw: str, *, field: str, default: float) -> float:
    text = raw.strip()
    if not text:
        return default
    try:
        value = float(text)
    except ValueError as error:
        raise ConfigError(f"{field} должен быть числом.") from error
    if value <= 0:
        raise ConfigError(f"{field} должен быть больше 0.")
    return value


def _parse_admin_ids(raw: str) -> tuple[int, ...]:
    text = raw.strip()
    if not text:
        return ()
    ids: list[int] = []
    for part in text.split(","):
        item = part.strip()
        if not item:
            continue
        try:
            value = int(item)
        except ValueError as error:
            raise ConfigError(
                "ADMIN_IDS должен быть списком числовых Telegram ID через запятую."
            ) from error
        if value <= 0:
            raise ConfigError("ADMIN_IDS содержит некорректный ID.")
        ids.append(value)
    # Уникальные, порядок сохранения.
    seen: set[int] = set()
    unique: list[int] = []
    for item in ids:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return tuple(unique)


def _parse_optional_int(raw: str, *, field: str) -> int | None:
    text = raw.strip()
    if not text:
        return None
    try:
        value = int(text)
    except ValueError as error:
        raise ConfigError(f"{field} должен быть целым числом.") from error
    if value <= 0:
        raise ConfigError(f"{field} должен быть больше 0.")
    return value


def load_settings(*, require_db: bool = True) -> Settings:
    _load_env()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise ConfigError(
            "Не задан BOT_TOKEN. Создайте файл .env в корне проекта "
            "и укажите токен бота от @BotFather. Пример есть в .env.example."
        )
    register_secret_value(token)

    password = os.getenv("DB_PASSWORD", "")
    register_secret_value(password.strip() or None)

    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    register_secret_value(openai_api_key or None)

    log_level = os.getenv("LOG_LEVEL", "INFO").strip() or "INFO"
    db_host = os.getenv("DB_HOST", "127.0.0.1").strip() or "127.0.0.1"
    db_port_raw = os.getenv("DB_PORT", "5432").strip() or "5432"
    try:
        db_port = int(db_port_raw)
    except ValueError as error:
        raise ConfigError("DB_PORT должен быть числом.") from error
    db_name = os.getenv("DB_NAME", "excursion_ai").strip() or "excursion_ai"
    db_user = os.getenv("DB_USER", "").strip()
    db_password = password

    openai_model = os.getenv("OPENAI_MODEL", "").strip()
    ai_enabled = _parse_bool(os.getenv("AI_ENABLED", "false"), field="AI_ENABLED")
    ai_daily_user_limit = _parse_positive_int(
        os.getenv("AI_DAILY_USER_LIMIT", "20"),
        field="AI_DAILY_USER_LIMIT",
        default=20,
    )
    ai_daily_global_limit = _parse_positive_int(
        os.getenv("AI_DAILY_GLOBAL_LIMIT", "200"),
        field="AI_DAILY_GLOBAL_LIMIT",
        default=200,
    )
    ai_admin_translation_daily_limit = _parse_positive_int(
        os.getenv("AI_ADMIN_TRANSLATION_DAILY_LIMIT", "50"),
        field="AI_ADMIN_TRANSLATION_DAILY_LIMIT",
        default=50,
    )
    ai_max_input_chars = _parse_positive_int(
        os.getenv("AI_MAX_INPUT_CHARS", "2000"),
        field="AI_MAX_INPUT_CHARS",
        default=2000,
    )
    ai_max_output_tokens = _parse_positive_int(
        os.getenv("AI_MAX_OUTPUT_TOKENS", "800"),
        field="AI_MAX_OUTPUT_TOKENS",
        default=800,
    )
    ai_timeout_seconds = _parse_positive_float(
        os.getenv("AI_TIMEOUT_SECONDS", "30"),
        field="AI_TIMEOUT_SECONDS",
        default=30.0,
    )
    ai_max_retries = _parse_positive_int(
        os.getenv("AI_MAX_RETRIES", "1"),
        field="AI_MAX_RETRIES",
        default=1,
    )
    retries_raw = os.getenv("AI_MAX_RETRIES", "1").strip()
    if retries_raw == "0":
        ai_max_retries = 0

    admin_ids = _parse_admin_ids(os.getenv("ADMIN_IDS", ""))
    catalog_cache_ttl_seconds = _parse_positive_int(
        os.getenv("CATALOG_CACHE_TTL_SECONDS", "60"),
        field="CATALOG_CACHE_TTL_SECONDS",
        default=60,
    )

    amocrm_enabled = _parse_bool(
        os.getenv("AMOCRM_ENABLED", "false"), field="AMOCRM_ENABLED"
    )
    amocrm_subdomain = os.getenv("AMOCRM_SUBDOMAIN", "").strip()
    amocrm_client_id = os.getenv("AMOCRM_CLIENT_ID", "").strip()
    amocrm_client_secret = os.getenv("AMOCRM_CLIENT_SECRET", "").strip()
    amocrm_redirect_uri = os.getenv("AMOCRM_REDIRECT_URI", "").strip()
    amocrm_access_token = os.getenv("AMOCRM_ACCESS_TOKEN", "").strip()
    amocrm_refresh_token = os.getenv("AMOCRM_REFRESH_TOKEN", "").strip()
    register_secret_value(amocrm_client_secret or None)
    register_secret_value(amocrm_access_token or None)
    register_secret_value(amocrm_refresh_token or None)
    token_store_raw = os.getenv("AMOCRM_TOKEN_STORE_PATH", "").strip()
    amocrm_token_store_path = token_store_raw or str(
        PROJECT_ROOT / "data" / "amocrm_tokens.json"
    )
    amocrm_pipeline_id = _parse_optional_int(
        os.getenv("AMOCRM_PIPELINE_ID", ""), field="AMOCRM_PIPELINE_ID"
    )
    amocrm_status_id = _parse_optional_int(
        os.getenv("AMOCRM_STATUS_ID", ""), field="AMOCRM_STATUS_ID"
    )
    amocrm_pipeline_name = os.getenv("AMOCRM_PIPELINE_NAME", "").strip()
    amocrm_status_name = os.getenv("AMOCRM_STATUS_NAME", "").strip()
    amocrm_cf_application_id = _parse_optional_int(
        os.getenv("AMOCRM_CF_APPLICATION_ID", ""),
        field="AMOCRM_CF_APPLICATION_ID",
    )
    amocrm_cf_excursion = _parse_optional_int(
        os.getenv("AMOCRM_CF_EXCURSION", ""),
        field="AMOCRM_CF_EXCURSION",
    )
    amocrm_cf_requested_date = _parse_optional_int(
        os.getenv("AMOCRM_CF_REQUESTED_DATE", ""),
        field="AMOCRM_CF_REQUESTED_DATE",
    )
    amocrm_cf_participants = _parse_optional_int(
        os.getenv("AMOCRM_CF_PARTICIPANTS", ""),
        field="AMOCRM_CF_PARTICIPANTS",
    )
    amocrm_cf_public_number = _parse_optional_int(
        os.getenv("AMOCRM_CF_PUBLIC_NUMBER", ""),
        field="AMOCRM_CF_PUBLIC_NUMBER",
    )
    amocrm_cf_session_date = _parse_optional_int(
        os.getenv("AMOCRM_CF_SESSION_DATE", ""),
        field="AMOCRM_CF_SESSION_DATE",
    )
    amocrm_cf_session_time = _parse_optional_int(
        os.getenv("AMOCRM_CF_SESSION_TIME", ""),
        field="AMOCRM_CF_SESSION_TIME",
    )
    amocrm_status_name_confirmed = os.getenv(
        "AMOCRM_STATUS_NAME_CONFIRMED", "Бронь подтверждена"
    ).strip() or "Бронь подтверждена"
    amocrm_status_name_cancelled = os.getenv(
        "AMOCRM_STATUS_NAME_CANCELLED", "Бронь отменена"
    ).strip() or "Бронь отменена"
    amocrm_booking_pipeline_name = os.getenv(
        "AMOCRM_BOOKING_PIPELINE_NAME", ""
    ).strip()
    amocrm_timeout_seconds = _parse_positive_float(
        os.getenv("AMOCRM_TIMEOUT_SECONDS", "30"),
        field="AMOCRM_TIMEOUT_SECONDS",
        default=30.0,
    )
    amocrm_max_attempts = _parse_positive_int(
        os.getenv("AMOCRM_MAX_ATTEMPTS", "5"),
        field="AMOCRM_MAX_ATTEMPTS",
        default=5,
    )
    amocrm_webhook_enabled = _parse_bool(
        os.getenv("AMOCRM_WEBHOOK_ENABLED", "false"),
        field="AMOCRM_WEBHOOK_ENABLED",
    )
    amocrm_webhook_host = os.getenv("AMOCRM_WEBHOOK_HOST", "0.0.0.0").strip() or "0.0.0.0"
    amocrm_webhook_port = _parse_positive_int(
        os.getenv("AMOCRM_WEBHOOK_PORT", "8080"),
        field="AMOCRM_WEBHOOK_PORT",
        default=8080,
    )
    amocrm_webhook_path = os.getenv("AMOCRM_WEBHOOK_PATH", "/amocrm/webhook").strip() or "/amocrm/webhook"
    amocrm_webhook_secret = os.getenv("AMOCRM_WEBHOOK_SECRET", "").strip()
    register_secret_value(amocrm_webhook_secret or None)

    settings = Settings(
        bot_token=token,
        log_level=log_level,
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
        db_user=db_user,
        db_password=db_password,
        openai_api_key=openai_api_key,
        openai_model=openai_model,
        ai_enabled=ai_enabled,
        ai_daily_user_limit=ai_daily_user_limit,
        ai_daily_global_limit=ai_daily_global_limit,
        ai_admin_translation_daily_limit=ai_admin_translation_daily_limit,
        ai_max_input_chars=ai_max_input_chars,
        ai_max_output_tokens=ai_max_output_tokens,
        ai_timeout_seconds=ai_timeout_seconds,
        ai_max_retries=ai_max_retries,
        admin_ids=admin_ids,
        catalog_cache_ttl_seconds=catalog_cache_ttl_seconds,
        amocrm_enabled=amocrm_enabled,
        amocrm_subdomain=amocrm_subdomain,
        amocrm_client_id=amocrm_client_id,
        amocrm_client_secret=amocrm_client_secret,
        amocrm_redirect_uri=amocrm_redirect_uri,
        amocrm_access_token=amocrm_access_token,
        amocrm_refresh_token=amocrm_refresh_token,
        amocrm_token_store_path=amocrm_token_store_path,
        amocrm_pipeline_id=amocrm_pipeline_id,
        amocrm_status_id=amocrm_status_id,
        amocrm_pipeline_name=amocrm_pipeline_name,
        amocrm_status_name=amocrm_status_name,
        amocrm_cf_application_id=amocrm_cf_application_id,
        amocrm_cf_excursion=amocrm_cf_excursion,
        amocrm_cf_requested_date=amocrm_cf_requested_date,
        amocrm_cf_participants=amocrm_cf_participants,
        amocrm_cf_public_number=amocrm_cf_public_number,
        amocrm_cf_session_date=amocrm_cf_session_date,
        amocrm_cf_session_time=amocrm_cf_session_time,
        amocrm_status_name_confirmed=amocrm_status_name_confirmed,
        amocrm_status_name_cancelled=amocrm_status_name_cancelled,
        amocrm_booking_pipeline_name=amocrm_booking_pipeline_name,
        amocrm_timeout_seconds=amocrm_timeout_seconds,
        amocrm_max_attempts=amocrm_max_attempts,
        amocrm_webhook_enabled=amocrm_webhook_enabled,
        amocrm_webhook_host=amocrm_webhook_host,
        amocrm_webhook_port=amocrm_webhook_port,
        amocrm_webhook_path=amocrm_webhook_path,
        amocrm_webhook_secret=amocrm_webhook_secret,
    )

    if require_db:
        settings.database_url()
    return settings
