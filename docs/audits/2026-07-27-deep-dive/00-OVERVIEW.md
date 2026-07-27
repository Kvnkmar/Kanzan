# CRM Clean-Room Deep Dive — 2026-07-27

> Method: CLAUDE.md + MEMORY.md **detached** (treated as untrusted). A 10-agent
> background workflow (8 subsystem readers + 2 adversarial verifiers, ~1.9M tokens)
> read the actual source at HEAD `df9b29d`, each told to CONFIRM/REFUTE/CORRECT
> every claim against on-disk files and hunt for new findings. Plus live Django-shell/DB
> probes, full `pytest`, and every quality gate re-run. Every count below states how it
> was measured. This folder is the detailed backing store for the CLAUDE.md rewrite.

## ⚠ The single most important fact: the working tree is BEHIND origin and PRE-hardening

| | |
|---|---|
| Local branch | `main` |
| Local HEAD | **`df9b29d`** "new updates" (2026-07-09 02:59) — child of `6c6662d` |
| `origin/main` | **`6e035de`** "Merge PR #2 go-live-hardening" (2026-07-22) |
| Relationship | local HEAD is an **ancestor** of origin/main → local is **5 commits behind** |
| Working tree | **4 dirty files, ALL frontend** (`custom-v15.css`, `base.html`, `landing_crm.html`, `dashboard.html`). No `.py` dirty → **backend == `df9b29d` exactly** |

**The 5 commits present on origin but ABSENT on disk:**

| Commit | Date | What it does (per commit message) |
|---|---|---|
| `73dfef1` | 2026-07-10 | Security + go-live hardening: bulk-action authz, onboarding, ops, audit fixes |
| `b37b3be` | 2026-07-10 | Fix zero-permission RBAC seeding (Bug A + Bug B) |
| `7c7b3de` | 2026-07-?? | Security hardening: tenant-membership gates, RBAC guards, XSS sanitization |
| `417d9cf` | | test(ci): add freezegun to dev requirements |
| `6e035de` | 2026-07-22 | Merge PR #2 |

**Consequence:** every security fix that the *prior* CLAUDE.md/MEMORY.md describe as "FIXED"
(zero-perm RBAC seeding, bulk-action authz, tenant-membership gates, XSS sanitization) is
**NOT in this working tree** — the pre-hardening bugs are live on disk. The prior docs
describe the *origin* state and are wrong for this checkout in many places. See
[01-SECURITY-FINDINGS.md](01-SECURITY-FINDINGS.md).

### The DB-vs-code smoking gun
`django_migrations` in `db.sqlite3` has **`accounts/0013_seed_full_permission_catalogue` APPLIED**,
but that migration file is **absent on disk** (`apps/accounts/migrations/` stops at `0012`). Proof
the DB was migrated by *newer* code than this checkout. The persisted DB therefore *looks* correctly
seeded (all 9 tenants have real per-role perms), while the on-disk seeding code is still buggy — a
freshly-provisioned tenant on THIS code gets 0-permission roles (reproduced live, twice).

## Quality gates (re-run 2026-07-27, all green except ruff non-blocking)

| Gate | Result |
|---|---|
| `pytest -q` | **1078 passed / 22 skipped / 1 xfailed** (210s, SQLite) |
| `makemigrations --check --dry-run` | clean ("No changes detected") |
| `manage.py check` | clean (0 issues) |
| `scripts/check_theme.py` | OK — **126** pre-existing hex literals tracked (baseline JSON = 145/10 files, stale-high) |
| `ruff check .` | **197** errors (157 fixable) — non-blocking, pre-existing |

## Authoritative counts (measured this pass)

- **91** first-party models across **21** apps with `models.py` (`apps.nav` is URL-only, no models)
- **122** migrations · **46** INSTALLED_APPS · **14** middleware · **12** Beat entries · **27** `@shared_task`
- **43** signal receivers · **76** admin registrations · **8** management commands · **17** exempt path prefixes
- **6** WebSocket consumers · **36** frontend routes · **29** `main/urls.py` path() · **23** `/api/v1/` includes (22 unique URLConfs)
- **48** `.html` templates · **14** JS modules / **6006** LOC · `custom-v15.css` **25,528** LOC (dirty) · `base.html` **318** (dirty)
- **76** test modules (69 root `tests/` + 7 app-level) · conftest 343 LOC (16 factories, 20 fixtures, 3 autouse)
- Enums: NotificationType **21** · INTERNAL_ONLY_TYPES **6** · ActivityLog.Action **34** (10 `email_*`) · TicketActivity.Event **27** · HubEmail **9** states/**4** priorities (no "medium") · InboundEmail.Status **9** · Reminder.Priority **4** (HAS "medium") · CallLog.Status **9** · Webhook.EventType **8** · custom_fields FieldType **8** / ModuleType **3**
- Permissions: `PERMISSION_DEFINITIONS`/`ALL_CODENAMES` = **69** codenames; DB has **71** rows (2 orphans: `ticket_category.view`, `ticket_status.view`); `ROLE_DEFINITIONS` = **6** (Viewer permission-less)
- ACTION_MAP = 76 entries / 75 unique keys (dup `mark_all_read` at permissions.py:46 & :95, both → view)
- `derive_palette` → **24** keys · base.py reads **~39-40** env keys (.env.example documents 16)
- DB: **9** tenants, per-role perms admin 69 / manager 59 / team-lead 40 / agent 24 / it 25 / hr 25 / viewer 0

## Environment
- Django 6.0.2 · DRF ≥3.16 · Channels 4 · Celery 5.4 · Python 3.12. Port 8001 (ASGI gunicorn+uvicorn).
- Dev DB SQLite (`db.sqlite3`); prod Postgres. Redis db3 (cache/sessions), db4 (Celery broker), db5 (Channels).
- `TIME_ZONE="Asia/Kuala_Lumpur"`, `CELERY_TIMEZONE="UTC"` (crontab beats fire on UTC → +8 local skew).
- venv `.venv/` (`env` is a symlink → `.venv`). `DJANGO_DEBUG` default False → prod.py; True → dev.py.

## Verifier corrections folded in
1. **C5 (Manager→Admin escalation) is REAL** — the tenancy study agent mislabeled `TenantMembershipViewSet` as "not vulnerable" (true only for cross-tenant *read*); it misses the vertical role-escalation. The security hunter + spot-check confirm C5.
2. **XSS refutation is scoped, not blanket** — inbound-email subject/body render paths all escape (textContent/DOMPurify), so that stored-XSS vector is REFUTED here; but `knowledge/views.py::preview_file` "sanitizes" mammoth-rendered DOCX with two weak regexes (strip `<script>`/`on\w+=`) — a credible stored-XSS vector. Do not claim "no XSS anywhere."
