# Deploying Excursion AI Bot with Docker Compose

Sample setup for a VPS: PostgreSQL (private Docker network), Telegram bot (polling + optional HTTP), nginx on port 80.

| Service | Role |
|---------|------|
| `bot` | Telegram polling + HTTP `:8080` inside the network (`/health`; webhook when enabled) |
| `proxy` | nginx on `:80` → `/health` (and `/amocrm/webhook` when you enable it) |
| `db` | PostgreSQL 18 on the internal network only (port **not** published) |

Keep `AMOCRM_ENABLED=false` until credentials are ready.  
Until you have HTTPS, keep `AMOCRM_WEBHOOK_ENABLED=false` and verify with `GET /health` only. Do not register an HTTP webhook URL that carries a secret in the query string.

---

## Files

| File | Purpose |
|------|---------|
| `Dockerfile` | App image |
| `docker-compose.yml` | `db` + `bot` + `proxy` |
| `.dockerignore` | Build exclusions |
| `deploy/entrypoint.sh` | Wait for DB → `alembic upgrade head` → bot |
| `deploy/nginx/default.conf` | Sample HTTP vhost (set your `server_name`) |
| `scripts/backup_local_db.ps1` | Local DB dump helper (Windows) |
| `scripts/restore_db.sh` | Restore a dump into the Compose `db` service |
| `.env.example` | Env template (no secrets) |

Secrets live only in `.env` on the machine (gitignored).

---

## 0. Copy the project to the server

On the server:

```bash
sudo mkdir -p /opt/excursion-ai/backups
sudo chown -R "$USER:$USER" /opt/excursion-ai
```

### Git Bash or WSL

Replace `USER` and `VPS_HOST`. From the project root:

```bash
rsync -avz --progress \
  --exclude '/.env' \
  --exclude '/.venv/' \
  --exclude '/venv/' \
  --exclude '/logs/' \
  --exclude '/__pycache__/' \
  --exclude '/.git/' \
  --exclude '/backups/' \
  --exclude '*.dump' \
  --exclude '/data/amocrm_tokens.json' \
  --exclude '/.idea/' \
  --exclude '/.vscode/' \
  "./" USER@VPS_HOST:/opt/excursion-ai/
```

If you also restore a database dump, copy it separately:

```bash
scp "backups/your_dump.dump" USER@VPS_HOST:/opt/excursion-ai/backups/
```

### PowerShell + `scp`

List sources separated by **spaces** (not commas). Do not copy `data/amocrm_tokens.json`. Prefer cloning from GitHub on the server when the repo is public.

**Do not copy to the server:** `.env`, `.venv/`, `logs/`, dumps (unless restoring), `data/amocrm_tokens.json`, IDE caches.

Create a **new** `.env` on the VPS from `.env.example`.

---

## 1. Local database backup (optional)

On a machine with Docker and a running local Postgres:

```powershell
.\scripts\backup_local_db.ps1
```

Produces `backups/excursion_ai_YYYYMMDD_HHMMSS.dump` (custom format). Do not commit dumps.

---

## 2. Server `.env`

```bash
cd /opt/excursion-ai
cp .env.example .env
nano .env
```

Required:

- `BOT_TOKEN`
- `DB_NAME=excursion_ai`
- `DB_USER=excursion_ai_app`
- `DB_PASSWORD=` (strong password; used by the Postgres container)
- `DB_HOST=db` and `DB_PORT=5432`
- `ADMIN_IDS=` (your Telegram numeric IDs)

First smoke test:

```env
AMOCRM_ENABLED=false
AMOCRM_WEBHOOK_ENABLED=false
AMOCRM_WEBHOOK_HOST=0.0.0.0
AMOCRM_WEBHOOK_PORT=8080
AMOCRM_WEBHOOK_PATH=/amocrm/webhook
AMOCRM_WEBHOOK_SECRET=
```

Edit `deploy/nginx/default.conf` and set `server_name` to your domain (or `_`).

---

## 3. Start on the VPS

Need: Docker Engine + Compose plugin, DNS A record to the VPS, firewall allowing **80/tcp** and SSH. Do **not** open PostgreSQL `5432` publicly.

```bash
cd /opt/excursion-ai
chmod +x scripts/restore_db.sh deploy/entrypoint.sh

docker compose up -d db

# Only if you have a dump AND a fresh empty pgdata volume:
# ./scripts/restore_db.sh backups/your_dump.dump

docker compose up -d --build
docker compose ps
curl -fsS http://YOUR_DOMAIN/health
# Expected: {"ok":true}
```

`restore_db.sh` waits for `pg_isready` and uses `--clean --if-exists`. Run it **once** on a new empty volume; do not re-run on a database you care about.

Telegram: message the bot `/start` (VPS needs outbound access to `api.telegram.org`).

Enable the amoCRM webhook only after HTTPS; do not register a secret-bearing HTTP URL in amoCRM.

---

## 4. Useful commands

```bash
docker compose ps
docker compose logs -f bot proxy db
docker compose restart bot
docker compose down          # keeps pgdata volume
docker compose down -v       # DESTROYS database volume
```
