# Excursion AI Bot

Demo Telegram assistant for browsing excursions, collecting booking requests, and optional CRM sync. Built as a portfolio / demo project — all offers and prices are fictional; it does **not** complete real paid bookings.

## Features

- **Catalog** from PostgreSQL (published excursions only), with RU / EN / IT UI strings
- **Application flow**: participants → session date/time → name → optional phone → confirmation
- **My applications** list for the Telegram user
- **AI consultant** (optional, OpenAI Responses API) to help choose an excursion
- **Admin panel** (Telegram IDs in `ADMIN_IDS`): create/edit drafts, translations, photos, publish/archive, session schedules and capacity
- **Session availability** (Europe/Rome): clients see only active future sessions; confirmed CRM bookings occupy seats
- **amoCRM integration** (optional): create/update leads, webhook for booking confirmed/cancelled stages
- **HTTP `/health`** for Docker/nginx checks; amoCRM webhook path when enabled

## Tech stack

- Python 3.10+ / 3.12 (Docker)
- [aiogram](https://docs.aiogram.dev/) 3.x
- PostgreSQL + SQLAlchemy 2 (async) + Alembic
- aiohttp (health + webhook)
- OpenAI SDK (optional)
- Docker Compose (app + Postgres + nginx)

## Project layout

| Path | Purpose |
|------|---------|
| `excursion_ai/` | Bot application code |
| `locales/` | RU / EN / IT interface strings |
| `alembic/` | DB migrations |
| `data/excursions.json` | One-time seed catalog (not used at runtime after import) |
| `deploy/` | Docker entrypoint, nginx sample, deploy notes |
| `scripts/` | DB role helper, local backup helpers |
| `tests/` | Scripts runnable with the project venv |
| `.env.example` | Environment variable template (no secrets) |

## Requirements

- Python 3.10+, PostgreSQL
- Telegram bot token from [@BotFather](https://t.me/BotFather)
- Optional: OpenAI API key + model name
- Optional: amoCRM OAuth app and pipeline configuration

## Local setup

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
# source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
# Edit .env: BOT_TOKEN, DB_*, ADMIN_IDS, …
```

Create the app DB role (adjust as needed) — see `scripts/create_app_role.sql` — then:

```bash
python -m alembic upgrade head
python -m excursion_ai.import_catalog   # seed demo excursions once
python -m excursion_ai                  # start polling (+ optional HTTP)
```

Validation without Telegram:

```bash
python -m excursion_ai.validate
```

## Environment variables

Copy `.env.example` to `.env`. Important groups:

| Group | Variables | Notes |
|-------|-----------|--------|
| Core | `BOT_TOKEN`, `LOG_LEVEL` | Required to start |
| Database | `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | Required |
| Admin | `ADMIN_IDS` | Comma-separated Telegram user IDs; empty = no admin access |
| Catalog | `CATALOG_CACHE_TTL_SECONDS` | In-memory cache TTL |
| AI | `AI_ENABLED`, `OPENAI_API_KEY`, `OPENAI_MODEL`, limits | Off until key + model are set |
| amoCRM API | `AMOCRM_ENABLED`, subdomain, OAuth tokens, pipeline names/IDs, custom field IDs | Off by default; applications stay in Postgres with `not_connected` |
| Webhook | `AMOCRM_WEBHOOK_ENABLED`, host/port/path/secret | Enable only behind HTTPS in production |

Do not commit `.env`, token store files, or dumps.

## Docker

```bash
cp .env.example .env
# Set BOT_TOKEN, DB_PASSWORD, ADMIN_IDS; use DB_HOST=db
# Keep AMOCRM_ENABLED=false and AMOCRM_WEBHOOK_ENABLED=false for a first smoke test

docker compose up -d --build
curl -fsS http://localhost/health
# Expect: {"ok":true}
```

Compose services:

- `db` — PostgreSQL 18 (named volume, **not** published to the host)
- `bot` — migrations on start, then polling + HTTP on internal `:8080`
- `proxy` — nginx on host `:80` → `/health` (and webhook path when enabled)

More detail: [`deploy/DEPLOY.md`](deploy/DEPLOY.md). Replace `server_name` in `deploy/nginx/default.conf` with your domain.

## Migrations and seed

```bash
python -m alembic upgrade head
python -m excursion_ai.import_catalog
```

Import validates `data/excursions.json`, preserves IDs, loads RU/EN/IT texts, and publishes seed excursions. Re-runs do not overwrite operator edits. Import is **not** run automatically on bot start.

Optional demo sessions for published excursions without upcoming slots:

```bash
python -m excursion_ai.seed_sessions_cli
```

## Tests

From the project root with `.venv` and `PYTHONPATH` set to the project directory:

```bash
# Windows PowerShell
$env:PYTHONPATH = (Get-Location).Path
.\.venv\Scripts\python.exe tests\test_menu.py
.\.venv\Scripts\python.exe tests\test_logging.py
.\.venv\Scripts\python.exe tests\test_nav_copy.py
.\.venv\Scripts\python.exe tests\test_ai.py
.\.venv\Scripts\python.exe tests\test_amocrm.py
.\.venv\Scripts\python.exe tests\test_admin_catalog.py
.\.venv\Scripts\python.exe tests\test_admin_wizard_hub.py
.\.venv\Scripts\python.exe tests\test_admin_translate_photos.py
.\.venv\Scripts\python.exe tests\test_apply_schedule_ux.py
```

Some suites (`test_db.py`, `test_apply.py`, schedule/capacity tests) need a reachable PostgreSQL configured in `.env`.  
`test_ai.py` and `test_amocrm.py` use mocked HTTP — they do not call real OpenAI/amoCRM.

## amoCRM: what is implemented vs what you must configure

**Implemented in code**

- Async lead/contact sync after an application is saved (`leads/complex` and updates when a lead id exists)
- Pipeline/status resolution by name or ID (no silent fallback to the default “purchase” pipeline)
- Custom fields (participants auto-create by name; optional field IDs for excursion, public number, session date/time)
- Inbound webhook for lead stage changes → confirm/cancel booking seat logic + optional user notify
- Token refresh with atomic file store (`AMOCRM_TOKEN_STORE_PATH`)
- Sync statuses: `not_connected` | `pending` | `sent` | `error` | `needs_review`

**Requires your configuration (not included in the repo)**

- `AMOCRM_ENABLED=true` and valid OAuth credentials / tokens
- Pipeline and status names or IDs matching your amoCRM account
- Public HTTPS URL for the webhook + `AMOCRM_WEBHOOK_SECRET`
- Optional custom field IDs in amoCRM settings

Until CRM is enabled, applications are stored only in PostgreSQL.

## Security notes

- Never commit `.env`, `data/amocrm_tokens.json`, database dumps, or logs
- Prefer HTTPS before enabling the amoCRM webhook (avoid secrets in plain HTTP query strings)
- Keep PostgreSQL on a private Docker network (this compose file does not publish port `5432`)

## License / disclaimer

Demo project for learning and portfolio use. Excursion content is fictional. No warranty for production use without your own hardening, compliance, and CRM/Telegram credentials.
