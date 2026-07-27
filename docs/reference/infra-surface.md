# Infra Surface — Verified 2026-05-22

> Verified against `main @ ea87bb2` (code state at `241e407`). Source of truth for **settings, middleware, ASGI, Celery, PM2, requirements, env, scripts, tests**. Pairs with `/CLAUDE.md`.

## Settings layout (`main/settings/`)

```
main/settings/
├── __init__.py     reads DJANGO_DEBUG, then loads dev.py overlay (True) or prod.py overlay (False)
├── base.py         all common config (see breakdown below)
├── dev.py          DEBUG=True, ALLOWED_HOSTS=["*"], disables WhiteNoise compression
└── prod.py         SECURE_SSL_REDIRECT, SECURE_PROXY_SSL_HEADER, HSTS, EMAIL_BACKEND=SMTP
```

`pytest.ini` sets `DJANGO_SETTINGS_MODULE=main.settings`, `pythonpath=.`. No `asyncio_mode` — pytest-asyncio defaults to `strict` (explicit decorators required).

### INSTALLED_APPS

Order: `daphne` (must be first) → `jazzmin` (must be before `django.contrib.admin`) → Django core (admin, auth, contenttypes, sessions, messages, staticfiles, postgres) → third-party (rest_framework, rest_framework_simplejwt + token_blacklist, django_filters, corsheaders, drf_spectacular, channels, django_celery_results, allauth + account + socialaccount + Google/Microsoft/OIDC providers, whitenoise.runserver_nostatic) → **21 project apps** (`main`, `tenants`, `accounts`, `api_keys`, `billing`, `tickets`, `contacts`, `kanban`, `comments`, `notifications`, `messaging`, `attachments`, `analytics`, `agents`, `custom_fields`, `knowledge`, `notes`, `inbound_email`, `crm`, `newsfeed`, `voip`).

> `django.contrib.sites` is **NOT** in INSTALLED_APPS. `ACCOUNT_LOGIN_METHODS = {"email"}` is a set. `AUTHENTICATION_BACKENDS` is not explicitly set — relies on Django defaults + allauth's import-time injection.

### MIDDLEWARE (14 layers, in order)

1. `SecurityMiddleware`
2. `WhiteNoiseMiddleware`
3. `SessionMiddleware`
4. `CorsMiddleware`
5. `CommonMiddleware`
6. `CsrfViewMiddleware`
7. `AuthenticationMiddleware`
8. `AccountMiddleware` (allauth)
9. **`SessionVersionMiddleware`** (custom — global logout via `User.auth_version`)
10. **`TenantMiddleware`** (resolves tenant from subdomain or `TenantSettings.domain`; sets `request.tenant`; binds to async-safe `contextvars` context. `/admin/` has a dedicated branch — NOT in `EXEMPT_PATH_PREFIXES` — that resolves the tenant from the subdomain when present, so subdomain-scoped admin gets `request.tenant` set.)
11. **`SubscriptionMiddleware`** (returns HTTP 402 when subscription is neither `is_active` nor `in_grace_period`)
12. **`apps.api_keys.middleware.RateLimitHeadersMiddleware`** ← NEW (emits `X-RateLimit-Limit/Remaining/Reset` from `request._crm_throttle_info`; zero overhead for non-API-key traffic)
13. `MessageMiddleware`
14. `XFrameOptionsMiddleware`

### `TenantMiddleware.EXEMPT_PATH_PREFIXES` (16 entries)

`/static/`, `/media/`, `/api/v1/accounts/auth/`, `/api/v1/billing/plans/`, `/api/v1/billing/webhook/`, `/api/v1/tickets/csat/`, `/api/docs/`, `/api/schema/`, `/accounts/`, `/inbound/email/`, `/login/`, `/register/`, `/logout/`, `/verify-email/`, `/verify-email-sent/`, `/setup-company/`, `/workspaces/`.

> **`/admin/` is NOT exempt** — dedicated branch resolves tenant from subdomain. `/auth/handoff/` is also intentionally NOT exempt — it must resolve the current tenant to verify membership.

### Database / cache / sessions

- **Database:** `env.db("DATABASE_URL", default=sqlite)` — `db.sqlite3` in dev; PostgreSQL via `psycopg[binary]` in prod
- **Cache:** Redis db3 (`KEY_PREFIX="crm"`) via `django-redis`
- **Sessions:** `cached_db` backend, host-only cookie (`SESSION_COOKIE_HTTPONLY=True`, SameSite=Lax)
- **Channels layer:** Redis db5 (prefix `crm:channels`) via `channels-redis`
- **Celery broker:** Redis db4; result backend `django-db` (django-celery-results)
- **TIME_ZONE:** `Asia/Kuala_Lumpur`; `USE_TZ=True`. Celery uses UTC.

### REST framework

- **`DEFAULT_AUTHENTICATION_CLASSES`** order: `JWTAuthentication` → **`apps.api_keys.authentication.APIKeyAuthentication`** → `SessionAuthentication` (JWT first; APIKey engages only when no `Bearer` header; Session as fallback)
- Permissions default: `IsAuthenticated`
- Pagination: `PageNumberPagination`, `PAGE_SIZE=50`
- Filters: `DjangoFilterBackend`, `SearchFilter`, `OrderingFilter`
- **Default throttle classes** (applied to every viewset): `ScopedRateThrottle`, **`apps.api_keys.throttling.APIKeyRateThrottle`**
- **Throttle rates:** `auth=10/min`, `api_default=200/min`, `api_heavy=30/min`, `webhook=60/min` (ScopedRateThrottle — only `AuthViewSet` opts in via `throttle_scope`), **`api_key=1000/hour`** (`APIKeyRateThrottle` — `SimpleRateThrottle`-based, auto-engages when `request.auth` is an `APIKey`)
- Schema: drf-spectacular (`SPECTACULAR_SETTINGS.TITLE="CRM Suite API"`). The `apps.api_keys.extensions.APIKeyAuthScheme` is registered by `apps/api_keys/apps.py::ready()` so Swagger UI's "Authorize" dialog shows an `ApiKeyAuth` option alongside the JWT bearer.

### Auth/SSO

`ACCOUNT_LOGIN_METHODS = {"email"}` (must be a `set`). Allauth providers configured: Google, Microsoft, OpenID Connect. SimpleJWT: 15m access, 7d refresh, rotate+blacklist, HS256.

## ASGI (`main/asgi.py`)

```
ProtocolTypeRouter
├── http       → get_asgi_application()
└── websocket  → AllowedHostsOriginValidator
                 → AuthMiddlewareStack
                   → WebSocketTenantMiddleware
                     → URLRouter(messaging_ws + notification_ws + ticket_ws + voip_ws + live_ws)
```

Six WebSocket endpoints (full breakdown in `api-surface.md`). `WebSocketTenantMiddleware` decodes the `Host` header from scope, resolves Tenant via subdomain or `domain` field, sets `scope["tenant"]` and binds `set_current_tenant()` for the lifetime of the connection (cleared in `finally:`).

## Celery (`main/celery.py`)

- App init reads `DJANGO_SETTINGS_MODULE`, then `app = Celery("main")`, `app.config_from_object("django.conf:settings", namespace="CELERY")`, `app.autodiscover_tasks()`
- **Queue routing** (`task_routes` — 6 globs + default):
  ```
  apps.billing.tasks.*                              → crm_webhooks    (dormant — apps/billing/tasks.py does not exist)
  apps.notifications.tasks.send_email_*             → crm_email
  apps.notifications.tasks.send_notification_email  → crm_email
  apps.inbound_email.tasks.*                        → crm_email
  apps.tickets.tasks.send_ticket_*                  → crm_email
  apps.api_keys.tasks.send_api_key_*                → crm_email
  apps.voip.tasks.*                                 → crm_voip
  *                                                 → crm_default
  ```
- **Default queue:** `crm_default`
- **Beat schedule:** 9 entries (full table in `codebase-inventory.md`)
- **Beat scheduler:** built-in shelve scheduler (file `celerybeat-schedule` at repo root). `django-celery-beat` was removed for Django 6 compatibility.
- Time limits: 300s hard, 240s soft. `worker_max_tasks_per_child=200`. JSON serialization. UTC timezone.

## PM2

### Production (`ecosystem.config.js`) — 5 processes

| Name                  | Script   | Args                                                                                                                                                | Memory | Kill timeout |
|-----------------------|----------|-----------------------------------------------------------------------------------------------------------------------------------------------------|--------|--------------|
| `crm-django`       | gunicorn | `main.asgi:application -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8001 --timeout 120 --graceful-timeout 30`                              | 2G     | 8000ms       |
| `crm-celery-worker`| celery   | `-A main worker -Q crm_default,crm_email,crm_webhooks -c 4 -l info --pool prefork -n crm-worker@%h --max-tasks-per-child=200`          | 2G     | 15000ms      |
| `crm-celery-beat`  | celery   | `-A main beat -l info`                                                                                                                              | 512M   | (default)    |
| `crm-flower`       | celery   | `-A main flower --port=5556 --url_prefix=flower --basic_auth=$CRM_FLOWER_AUTH`                                                                   | 512M   | (default)    |
| `crm-smtp`         | python   | `manage.py run_smtp_server`                                                                                                                         | 512M   | 8000ms       |

Common: `cwd=/home/kavin/CRM`, `exec_mode=fork`, `watch=false`, `autorestart=true (max 10 restarts, min 10s uptime)`, `merge_logs=true`.

> **Caveat:** `crm_voip` queue is in Celery routes but **not in worker `-Q`** — VoIP Celery tasks won't run unless the queue is added or a dedicated VoIP worker is started. `run_ari_listener` is **not in PM2** by default — start separately if VoIP is live. Makefile `stop` and `restart` targets omit `crm-smtp` — manage that one independently.

### Development (`ecosystem.dev.config.js`) — 4 processes (no SMTP)

- `crm-django` uses `python manage.py runserver 0.0.0.0:8001` for autoreload
- `crm-celery-worker` watches `apps/*/tasks.py`, `apps/*/services.py`, `main/celery.py` (watch_delay 2s); concurrency `-c 2 --max-tasks-per-child=50`
- Lower memory caps; `max_restarts=50`, `min_uptime="3s"`
- **VENV-path discrepancy:** dev config references `env/` while prod uses `.venv/`. The repo has a symlink `env -> .venv` so both paths resolve to the same venv on this machine.

## Requirements

### `requirements/base.txt` (production-grade pins, all `>=X,<Y`)
- Django==6.0.2 · djangorestframework>=3.16.1,<4 · drf-spectacular>=0.28,<0.29
- channels>=4.2,<5 · channels-redis>=4.2,<5
- celery>=5.4,<6 · django-celery-results>=2.5,<3 (django-celery-beat removed)
- django-redis>=5.4,<6 · redis>=5.2,<6 · psycopg[binary]>=3.2,<4
- gunicorn>=25,<26 · uvicorn[standard]>=0.40,<1 · daphne>=4.2,<5 · flower>=2.0,<3
- stripe>=11,<12 · django-allauth>=65,<66 · rest_framework_simplejwt>=5.4,<6 · PyJWT>=2.9,<3
- python-magic>=0.4,<0.5 · Pillow>=11,<12 · whitenoise>=6.8,<7 · mammoth>=1.12,<2 · openpyxl>=3.1,<4
- django-environ>=0.12,<1 · django-jazzmin>=3.0,<4 · django-filter>=24.3,<25 · django-cors-headers>=4.6,<5
- aiosmtpd>=1.4,<2 · httpx>=0.27,<1 · websockets>=12,<14

### `requirements/dev.txt` adds
- pytest>=8.3,<9 · pytest-django>=4.9,<5 · pytest-asyncio>=0.24,<1 · pytest-cov>=6,<7
- factory-boy>=3.3,<4 · faker>=33,<34
- ruff>=0.8,<1
- django-debug-toolbar>=4.4,<5 · django-extensions>=3.2,<4 · ipython>=8.31,<9

### `requirements/prod.txt`
**Literally `-r base.txt` — zero production extras.**

### Root `requirements.txt`
Byte-identical duplicate of `requirements/base.txt` (convenience for tools that default to `./requirements.txt`, e.g. Render, some Heroku buildpacks). Keep them in sync.

## Environment variables

`.env.example` (16 keys): `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DATABASE_URL`, `REDIS_URL`, `BASE_DOMAIN`, `BASE_SCHEME`, `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`, `JWT_SECRET_KEY`, `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `CRM_FLOWER_AUTH`.

`base.py` also reads (NOT in `.env.example`): `BASE_PORT`, `EMAIL_BACKEND`, `DEFAULT_FROM_EMAIL`, `EMAIL_TIMEOUT`, `EMAIL_USE_SSL`, `IMAP_HOST` / `_PORT` / `_USER` / `_PASSWORD` / `_MAILBOX` / `_USE_SSL` / `_DEFAULT_TENANT_SLUG`, `SMTP_SERVER_HOST` / `_PORT` / `_HOSTNAME` / `_REQUIRE_AUTH` / `_AUTH_USERS` (JSON dict) / `_TLS_CERT_FILE` / `_TLS_KEY_FILE`, `INBOUND_EMAIL_WEBHOOK_SECRET`.

## Makefile (~25 documented targets)

`help`, `dev`, `dev-stop`, `start`, `stop`, `restart`, `status`, `restart-django`, `restart-workers`, `restart-celery`, `restart-backend`, `restart-all`, `migrate`, `makemigrations`, `migrate-check`, `migrate-full`, `collectstatic`, `shell`, `dbshell`, `test`, `test-fast`, `test-cov`, `lint`, `lint-fix`, `format`, `logs`, `logs-celery`, `logs-all`, `smoke`, `check`, `theme-check`, `theme-check-strict`, `theme-baseline`.

> Pre-commit gate is `make check` (lint + migrate-check + test). **No CI/CD** — no `.github/`, no Dockerfile, no docker-compose. Deployment is PM2 on a single host.

## Scripts

- `scripts/check_theme.py` — regression guard for theme leakage. Scans static/templates for new off-token hex literals against `scripts/.theme_baseline.json`. CSS comments + `:root`/`[data-bs-theme]` blocks + HTML `{% %}` tags + `<input type="color">` defaults + `data-*="hex"` attrs + entire `<script>` blocks are masked. **`.js` files have NO additional masking — JS hex IS flagged.** Allowlist: `apps/tenants/colors.py`, `scripts/check_theme.py`, `templates/pages/settings/tenant.html`, `static/js/theme.js`, `static/css/custom.css` + 4 email template dirs.
- `scripts/.theme_baseline.json` — current tolerated count: **127 hex literals across 8 files** (custom-v15.css 83, landing_crm 21, verify_email_sent 3, calendar 2, kanban 1, landing 15, reminders 1, tickets-list 1).

## Tests

- **61 test modules total**: 54 root-level `tests/test_*.py` + 7 app-level
  - `apps/knowledge/tests/test_kb_gap_fill.py`
  - `apps/tickets/tests/test_creation.py`, `test_escalation.py`
  - `apps/api_keys/tests/test_authentication.py`, `test_documentation.py`, `test_throttling.py`, `test_viewset.py` (**43 tests across 4 files**)
- `conftest.py` defines **16 factories** + **20 fixtures** (3 autouse: `celery_eager`, `free_plan`, `clear_tenant_context`). `RoleFactory` declares 4 traits (admin/manager/agent/viewer); the new `team-lead`/`it`/`hr` roles are picked up via `Role.unscoped.get(slug=…)` from the signal-seeded set.
- `tests/base.py` (legacy) provides `TenantTestCase` (tenant_a/tenant_b + admin/agent/viewer per tenant) and extended `CRMBaseTestCase`
- **API-keys test pattern:** `apps/api_keys/tests/test_viewset.py::test_email_task_queued_on_create` wraps the POST in `django_capture_on_commit_callbacks(execute=True)` because `transaction.on_commit` callbacks are otherwise discarded by `pytest.mark.django_db`'s atomic-rollback teardown. Canonical pattern for exercising post-commit Celery dispatch under pytest-django.
- `pytest.ini` has no `asyncio_mode` set — defaults to `strict` (explicit `@pytest.mark.asyncio` required).

## Repo runtime / build artifacts

- `db.sqlite3` (~12 MB) — dev DB, in `.gitignore`
- `celerybeat-schedule` (~36 KB) — Beat shelve state, in `.gitignore`
- `logs/` (~33 MB total — `celery-worker-error.log` alone is 16 MB) — PM2 process logs, in `.gitignore`. **No log rotation configured** — add `pm2 install pm2-logrotate` or `/etc/logrotate.d/` entry before disk pressure.
- `tmp/emails/` — dev filebased EmailBackend output (only present when emails are sent), in `.gitignore`
- `media/tenants/` and `media/inbound_emails/` — tenant uploads + inbound attachments
- `__pycache__/` (root) — pytest-imported `conftest.py` bytecode (regenerated on test run)
- `.pytest_cache/` — pytest internals, in `.gitignore`

## Quick reference

```
Superuser:      admin@crm.local / Pl@nC-ICT_2024
Django Admin:   http://localhost:8001/admin/   (locked to is_superuser — see main/admin.py)

Tenants:
  Straat-X:     http://straat-x.localhost:8001

Flower:         http://localhost:5556 (admin:changeme — CRM_FLOWER_AUTH)
API Docs:       http://straat-x.localhost:8001/api/docs/   (Authorize dialog shows both JWT Bearer and ApiKeyAuth)
SMTP (in-process): port 2525 — `crm-smtp` PM2 process
```
