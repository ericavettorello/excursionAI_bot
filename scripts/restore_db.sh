#!/usr/bin/env bash
# Восстановление дампа в контейнер db (на VPS).
# Пример: ./scripts/restore_db.sh backups/excursion_ai_YYYYMMDD_HHMMSS.dump
#
# Запускайте только на новом пустом томе pgdata.
# --clean --if-exists удаляет объекты из дампа перед восстановлением —
# повторный запуск на рабочей БД затрёт текущие данные снимком.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DUMP="${1:-}"
if [[ -z "$DUMP" || ! -f "$DUMP" ]]; then
  echo "Usage: $0 path/to/excursion_ai_*.dump" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "Создайте .env на сервере перед восстановлением." >&2
  exit 1
fi

# shellcheck disable=SC1091
set -a
source .env
set +a

: "${DB_NAME:?DB_NAME required}"
: "${DB_USER:?DB_USER required}"
: "${DB_PASSWORD:?DB_PASSWORD required}"

echo "Ensuring db container is up…"
docker compose up -d db
docker compose exec -T db sh -c 'until pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"; do sleep 1; done'

echo "Restoring $DUMP into $DB_NAME (existing objects may be replaced)…"
# --clean --if-exists: безопасно для повторного прогона на той же БД.
docker compose exec -T db pg_restore \
  -U "$DB_USER" \
  -d "$DB_NAME" \
  --clean \
  --if-exists \
  --no-owner \
  --no-acl \
  < "$DUMP"

echo "OK restore finished. Next: docker compose up -d bot proxy"
echo "Alembic upgrade head выполнится при старте bot (entrypoint)."
