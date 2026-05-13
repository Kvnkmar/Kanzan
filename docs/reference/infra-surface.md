# Infra Surface — Verified 2026-05-11

> Verified against `main @ bb36325`. Source of truth for **settings, middleware, ASGI, Celery, PM2, requirements, env, scripts**.

## Settings layout (`main/settings/`)

```
main/settings/
├── __init__.py     reads DJANGO_DEBUG, then loads dev.py overlay (True) or prod.py overlay (False)
├── base.py         all common config (see breakdown below)
├── dev.py          DEBUG=True, ALLOWED_HOSTS=["*"], disables WhiteNoise compression
└── prod.py         SECURE_SSL_REDIRECT, SECURE_PROXY_SSL_HEADER, HSTS, EMAIL_BACKEND=SMTP
```

`pytest.ini` sets `DJANGO_SETTINGS_MODULE=main.settings`, `pythonpath=.`.

### INSTALLED_APPS (27 entries)

Order: `daphne` (must be first) → `jazzmin` (must be before `django.contrib.admin`) → Django core (admin, auth, contenttypes, sessions, messages, staticfiles, postgres) → third-party (rest_framework, rest_framework_simplejwt + token_blacklist, django_filters, corsheaders, drf_spectacular, channels, django_celery_results, allauth + account + socialaccount + Google/Microsoft/OIDC providers, whitenoise.runserver_nostatic) → 20 project apps (`main`, `tenants`, `accounts`, `billing`, `tickets`, `contacts`, `kanban`, `comments`, `notifications`, `messaging`, `attachments`, `analytics`, `agents`, `custom_fields`, `knowledge`, `notes`, `inbound_email`, `crm`, `newsfeed`, `voip`).

### MIDDLEWARE (13 layers, in order)

1. `SecurityMiddleware`
2. `WhiteNoiseMiddleware`
3. `SessionMiddleware`
4. `CorsMiddleware`
5. `CommonMiddleware`
6. `CsrfViewMiddleware`
7. `AuthenticationMiddleware`
8. `AccountMiddleware` (allauth)
9. **`SessionVersionMiddleware`** (custom — global logout via `User.auth_version`)
10. **`TenantMiddleware`** (resolves tenant from subdomain or `TenantSettings.domain`; sets `request.tenant`; binds to async-safe `contextvars` context)
11. **`SubscriptionMiddleware`** (returns HTTP 402 when subscription is neither `is_active` nor `in_grace_period`)
12. `MessageMiddleware`
13. `XFrameOptionsMiddleware`

### Database / cache / sessions

- **Database:** `env.db("DATABASE_URL", default=sqlite)` — `db.sqlite3` in dev; PostgreSQL via `psycopg[binary]` in prod
- **Cache:** Redis db3 (`KEY_PREFIX="kanzan"`) via `django-redis`
- **Sessions:** `cached_db` backend, host-only cookie (`SESSION_COOKIE_HTTPONLY=True`, SameSite=Lax)
- **Channels layer:** Redis db5 (prefix `kanzan:channels`) via `channels-redis`
- **Celery broker:** Redis db4; result backend `django-db` (django-celery-results)
- **TIME_ZONE:** `Asia/Kuala_Lumpur`; `USE_TZ=True`. Celery uses UTC.

### REST framework

- Auth: SimpleJWT (15m access, 7d refresh, rotate+blacklist, HS256) + SessionAuthentication
- Permissions default: IsAuthenticated
- Pagination: PageNumberPagination, PAGE_SIZE=50
- Filter: DjangoFilterBackend, SearchFilter, OrderingFilter
- Throttle: ScopedRateThrottle — `auth=10/min`, `api_default=200/min`, `api_heavy=30/min`, `webhook=60/min`
- Schema: drf-spectacular (`/api/schema/` JSON, `/api/docs/` Swagger)

### Auth/SSO

`ACCOUNT_LOGIN_METHODS = {"email"}` (must be a `set`). Allauth providers configured: Google, Microsoft, OpenID Connect.

## ASGI (`main/asgi.py`)

```
ProtocolTypeRouter
├── http       → get_asgi_application()
└── websocket  → AllowedHostsOriginValidator
                 → AuthMiddlewareStack
                   → WebSocketTenantMiddleware
                     → URLRouter(messaging_ws + notification_ws + ticket_ws + voip_ws)
```

Five WebSocket consumers (full breakdown in `api-surface.md`).

## Celery (`main/celery.py`)

- App init reads `DJANGO_SETTINGS_MODULE`, then `app = Celery("main")`, `app.config_from_object("django.conf:settings", namespace="CELERY")`, `app.autodiscover_tasks()`
- **Queue routing** (`task_routes`):
  ```
  apps.billing.tasks.*                              → kanzan_webhooks
  apps.notifications.tasks.send_email_*             → kanzan_email
  apps.notifications.tasks.send_notification_email  → kanzan_email
  apps.inbound_email.tasks.*                        → kanzan_email
  apps.tickets.tasks.send_ticket_*                  → kanzan_email
  apps.voip.tasks.*                                 → kanzan_voip
  *                                                 → kanzan_default
  ```
- **Default queue:** `kanzan_default`
- **Beat schedule:** 9 entries (full table in `codebase-inventory.md`)
- **Beat scheduler:** built-in shelve scheduler (file `celerybeat-schedule` at repo root). `django-celery-beat` was removed for Django 6 compatibility.
- Time limits: 300s hard, 240s soft. `worker_max_tasks_per_child=200`. JSON serialization. UTC timezone.

## PM2

### Production (`ecosystem.config.js`) — 5 processes

| Name                  | Script   | Args                                                                                                                                                | Memory | Kill timeout |
|-----------------------|----------|-----------------------------------------------------------------------------------------------------------------------------------------------------|--------|--------------|
| `kanzan-django`       | gunicorn | `main.asgi:application -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8001 --timeout 120 --graceful-timeout 30`                              | 2G     | 8000ms       |
| `kanzan-celery-worker`| celery   | `-A main worker -Q kanzan_default,kanzan_email,kanzan_webhooks -c 4 -l info --pool prefork -n kanzan-worker@%h --max-tasks-per-child=200`          | 2G     | 15000ms      |
| `kanzan-celery-beat`  | celery   | `-A main beat -l info`                                                                                                                              | 512M   | (default)    |
| `kanzan-flower`       | celery   | `-A main flower --port=5556 --url_prefix=flower --basic_auth=$KANZAN_FLOWER_AUTH`                                                                   | 512M   | (default)    |
| `kanzan-smtp`         | python   | `manage.py run_smtp_server`                                                                                                                         | 512M   | 8000ms       |

Common: `cwd=/home/kavin/Kanzen`, `exec_mode=fork`, `watch=false`, `autorestart=true (max 10 restarts, min 10s uptime)`, `merge_logs=true`.

> **Caveat:** `kanzan_voip` queue is in Celery routes but **not in worker `-Q`** — VoIP Celery tasks won't run unless the queue is added or a dedicated VoIP worker is started. `run_ari_listener` is **not in PM2** by default — start separately if VoIP is live.

### Development (`ecosystem.dev.config.js`) — 4 processes (no SMTP)

- `kanzan-django` uses `python manage.py runserver 0.0.0.0:8001` for autoreload
- `kanzan-celery-worker` watches `apps/*/tasks.py`, `apps/*/services.py`, `main/celery.py` (watch_delay 2s); concurrency `-c 2`
- Lower memory caps
- **VENV-path discrepancy:** dev config references `env/` while prod uses `.venv/`. The repo has a symlink `env -> .venv` so both paths resolve to the same venv on this machine.

## Requirements (`requirements/`)

`base.txt` (production-grade pins, all `>=X,<Y`):
- Django==6.0.2 · djangorestframework>=3.16.1,<4 · drf-spectacular>=0.28,<0.29
- channels>=4.2,<5 · channels-redis>=4.2,<5
- celery>=5.4,<6 · django-celery-results>=2.5,<3 (django-celery-beat removed)
- django-redis>=5.4,<6 · redis>=5.2,<6 · psycopg[binary]>=3.2,<4
- gunicorn>=25,<26 · uvicorn[standard]>=0.40,<1 · daphne>=4.2,<5 · flower>=2.0,<3
- stripe>=11,<12 · django-allauth>=65,<66 · rest_framework_simplejwt>=5.4,<6 · PyJWT>=2.9,<3
- python-magic>=0.4,<0.5 · Pillow>=11,<12 · whitenoise>=6.8,<7 · mammoth>=1.12,<2 · openpyxl>=3.1,<4
- django-environ>=0.12,<1 · django-jazzmin>=3.0,<4 · django-filter>=24.3,<25 · django-cors-headers>=4.6,<5
- aiosmtpd>=1.4,<2 · httpx>=0.27,<1 · websockets>=12,<14

`dev.txt` adds:
- pytest>=8.3,<9 · pytest-django>=4.9,<5 · pytest-asyncio>=0.24,<1 · pytest-cov>=6,<7
- factory-boy>=3.3,<4 · faker>=33,<34
- ruff>=0.8,<1
- django-debug-toolbar>=4.4,<5 · django-extensions>=3.2,<4 · ipython>=8.31,<9

`prod.txt` adds production extras (e.g., sentry-sdk if used; check the file when deploying).

## Environment variables

`.env.example` (16 keys): `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DATABASE_URL`, `REDIS_URL`, `BASE_DOMAIN`, `BASE_SCHEME`, `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`, `JWT_SECRET_KEY`, `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `KANZAN_FLOWER_AUTH`.

`base.py` also reads (NOT in `.env.example`): `BASE_PORT`, `EMAIL_BACKEND`, `DEFAULT_FROM_EMAIL`, `EMAIL_TIMEOUT`, `EMAIL_USE_SSL`, `IMAP_HOST` / `_PORT` / `_USER` / `_PASSWORD` / `_MAILBOX` / `_USE_SSL` / `_DEFAULT_TENANT_SLUG`, `SMTP_SERVER_HOST` / `_PORT` / `_HOSTNAME` / `_REQUIRE_AUTH` / `_AUTH_USERS` (JSON dict) / `_TLS_CERT_FILE` / `_TLS_KEY_FILE`, `INBOUND_EMAIL_WEBHOOK_SECRET`.

Current dev `.env` includes Gmail SMTP/IMAP credentials (`kvnkmar012@gmail.com`) and uses `IMAP_DEFAULT_TENANT_SLUG=dpap` so unresolvable inbound mail routes to the `dpap` tenant.

## Makefile (22 targets)

`help`, `dev`, `dev-stop`, `start`, `stop`, `restart`, `status`, `restart-django`, `restart-workers`, `restart-celery`, `restart-backend`, `restart-all`, `migrate`, `makemigrations`, `migrate-check`, `migrate-full`, `collectstatic`, `shell`, `dbshell`, `test`, `test-fast`, `test-cov`, `lint`, `lint-fix`, `format`, `logs`, `logs-celery`, `logs-all`, `smoke`, `check`.

## Tests (`tests/` + `apps/*/tests/`)

- 54 root-level test modules (`tests/test_*.py`) — 13,368 lines total
- 3 app-level test modules:
  - `apps/tickets/tests/test_creation.py` (625 lines)
  - `apps/tickets/tests/test_escalation.py` (600 lines)
  - `apps/knowledge/tests/test_kb_gap_fill.py` (125 lines)
- **57 modules total**
- `conftest.py` defines **16 factories** (Tenant, User, Role with admin/manager/agent/viewer traits, Membership, TicketStatus, Queue, Ticket, Company, Contact, ContactGroup, Notification, Plan, Subscription, CustomFieldDefinition, Reminder, InboundEmail) and **20 fixtures** (3 autouse: `celery_eager`, `free_plan`, `clear_tenant_context`)
- `tests/base.py` (299 lines) provides legacy `TenantTestCase` (tenant_a/tenant_b + admin/agent/viewer per tenant) and extended `KanzenBaseTestCase` (adds manager_a, agent_b, full status set, SLA policy, queue, contact)

## Database (verified via `sqlite3 db.sqlite3 ".tables"`)

- **112 tables** in dev DB
- 29 apps with migrations recorded in `django_migrations`
- Tickets has the highest count: 26 migrations
- `oauth2_provider` (13), `auth` (12), `token_blacklist` (12), `django_celery_results` (14) come from third-party apps

## Repo runtime / build artifacts

- `db.sqlite3` (12 MB) — dev DB, in `.gitignore`
- `celerybeat-schedule` (36 KB) — Beat shelve state, in `.gitignore`
- `logs/` (~9 MB total) — PM2 process logs (one per process, error+out variants), in `.gitignore`
- `tmp/emails/` — dev filebased EmailBackend output (only present when emails are sent), in `.gitignore`
- `media/tenants/` and `media/inbound_emails/` — tenant uploads + inbound attachments
- `__pycache__/` (root) — pytest-imported `conftest.py` bytecode (regenerated on test run)
- `.pytest_cache/` — pytest internals, in `.gitignore`
- `scripts/` — empty placeholder folder

## Quick reference

```
Superuser:      admin@epstein.local / Pl@nC-ICT_2024
Django Admin:   http://localhost:8001/admin/

Tenants:
  DPAP:         http://dpap.localhost:8001      (custom domain: asmra.shop)
  Meeting:      http://meeting.localhost:8001
  Debug:        http://debug-test.localhost:8001

Flower:         http://localhost:5556 (admin:changeme)
API Docs:       http://dpap.localhost:8001/api/docs/
```
