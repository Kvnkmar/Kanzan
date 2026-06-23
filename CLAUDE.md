# Kanzen — Project Intelligence

> Last refreshed: **2026-06-23** — full independent re-derivation pass (CLAUDE.md + MEMORY.md **detached**; 6 parallel agents each re-derived the load-bearing facts FRESH from code with NO access to these docs; every diff in the working tree read line-by-line; full `pytest` run + every quality gate re-executed). Verified against branch **`main`** @ HEAD **`94468bc`** (== `origin/main`, the last *commit* is synced) — but the **working tree is DIRTY** (see below).
>
> **⚠️ The working tree carries ONE cohesive batch of uncommitted work (20 modified tracked files + 1 untracked migration).** It layers THREE logical changes on top of `94468bc`. Do not assume `git HEAD` reflects what's on disk — several load-bearing facts below describe the **on-disk** state, which differs from the last commit.
>
> 1. **Feature C — "agent-decides ticket confirmation email" + instant ReminderScheduler.** `TenantSettings.auto_send_ticket_created_email` default flipped **True → False**; the inbound auto-send guard inverted to "send only when explicitly on"; an untracked migration `tenants/0011` (`AlterField` + `RunPython` disabling existing True rows); the settings toggle no longer renders `checked`; a new client-side `ReminderScheduler` IIFE in `app.js` fires the reminder-due popup at the *exact* due time. See **§Feature C**.
> 2. **"Emails / Inbox" UI rename + route swap.** The two email surfaces were **relabelled and their frontend routes swapped** (the triage cockpit is now **"Emails"** at **`/emails/`**; the personal page is now **"Inbox"** at **`/inbox/`**). View funcs / template dirs / API / the `inbox_hub` app are kept (intentional name↔label mismatch). The cockpit also gained a real **assignee chip** feature and bumped to `inbox-hub.js ?v=13`. See **§Emails/Inbox Rename**.
> 3. **Two behaviour reversals (NEW — the old docs described the OPPOSITE):**
>    - **`tickets/access.py` — a creator now KEEPS a ticket after handing it off.** `agent_visible_tickets_q` is now `Q(assignee=user) | Q(created_by=user)` (was `… & Q(assignee__isnull=True)`). A triager who converts an inbound email and routes it to a teammate **still sees it** in their own list/detail. **This reverses the prior "self-created ticket handed off LEAVES the creator's view" rule.**
>    - **`TICKET_ASSIGNED` is now in `INTERNAL_ONLY_TYPES`** — ticket-assignment notifications are now **in-app/WS only, never emailed** (the emailed copy was redundant). `INTERNAL_ONLY_TYPES` grew **5 → 6**.
>
> **Quality gates (re-run 2026-06-23 against the dirty working tree):** full `pytest -q` = **1048 passed / 0 failed / 22 skipped / 1 xfailed** ✅ **GREEN** (~212s on SQLite; 1071 collected). `makemigrations --check --dry-run` = **"No changes detected" (exit 0)** — the untracked `tenants/0011` covers the model change. `python scripts/check_theme.py` **PASSES** ("146 pre-existing hex literals tracked", baseline 147/11). `ruff check .` = **197 errors** (157 auto-fixable; mostly F401/F841/F541 — non-blocking). CI runs the same gates against **PostgreSQL 16 + Redis 7** on every push-to-`main` + PR (ruff `continue-on-error`; migrate-check / theme-check / pytest blocking).
>
> **✅ Gaps the historical docs flagged that are now CLOSED on `main` (re-verified 2026-06-23):** authed `/media/` (`apps/attachments/media_views.py::serve_protected_media`, X-Accel in prod); `main/checks.py::kanzen.E001` hard-fails a deploy check when `DEBUG is True`; Company custom-fields synced (`custom_fields/signals.py` `Company.post_save`); internal-comment bodies redacted on the wire (`comments/signals.py` → `body: None if is_internal`); Hub `first_responded_at` stamped by `transition_hub_email`; billing VoIP entitlement seeded (mig `billing/0003`) + re-subscribe repoint; messaging cross-tenant user scoping; IMAP per-`(tenant, message_id)` dedup; first CI; API-register email-verify bypass; Stripe webhook replay guard; message-edit silent-no-op; 3 unwired webhook events.
>
> **Still-open footguns (pre-existing, carried forward — re-confirmed present 2026-06-23):** raw `role` vs `effective_role` drift at **4 sites** — `agents/services.py:226` (`pick_email_agent`), `inbox_hub/assignment.py:228` (`_candidate_user_ids` no-dept fallback), `tickets/views.py:1052` & `:1115` (`teammates`/`team_progress`, raw `role__hierarchy_level__lte=30`); model `clean()` validators never called by `save()` (`Ticket` assignee, `Account.health_score`, `Contact.lead_score`); `check_overdue_reminders` + `check_sla_breach_warnings` DEAD (not in Beat); `kanzan_voip` queue unconsumed (worker `-Q` excludes it) + `run_ari_listener` not in PM2 + `CallEventConsumer` tenant-wide (no per-user scoping); logs unrotated (no rotation); `KBRevision`/`KBTicketLink`/`require_feature`/`PARKED_IN_HUB` dead; `escalate_hub_email` bumps `escalation_count` even on illegal transition; Hub SLA is wall-clock (no business-hours math); cockpit never calls `transition`/`claim`/`escalate`/`note` (so Hub `first_responded_at` rarely stamped in practice → response-breach usually fires + auto-escalates); `ContactEvent` emits no live events; command-palette `/contacts/new/` dead link; XLSX-without-openpyxl writes CSV into a `.xlsx`; `make logs-django` errors (in `.PHONY`, no rule body); `make stop`/`restart` skip `kanzan-smtp`; `pytest-timeout` missing from `dev.txt` (so `make test-fast` errors); `analytics.DashboardView` is `IsAuthenticated`-only. These are the QA audit's **Sprint 1–3** backlog (`docs/qa-audit-2026-06-14/06-REMEDIATION-PLAN.md`).
>
> **Re-verified factual counts (2026-06-23, authoritative — via Django app registry / wc / grep, NOT trusting the prior doc):**
> - **91 Django model classes** across 21 apps with `models.py` (per-app, from `apps.get_models()`: tickets 22, accounts 8, inbox_hub 8, knowledge 6, contacts 5, voip 5, billing 4, comments 4, analytics 4, kanban 3, messaging 3, inbound_email 3, newsfeed 3, tenants 2, notifications 2, agents 2, custom_fields 2, crm 2, api_keys 1, attachments 1, notes 1). 2 abstract bases (`TimestampedModel`, `TenantScopedModel`). 5 polymorphic GenericFK models (Attachment, Comment, ActivityLog, CustomFieldValue, CardPosition).
> - **120 migration files on disk** (119 committed + **1 untracked** = `tenants/0011_auto_send_ticket_created_email_default_false`). `makemigrations --check` clean. `apps.nav`/`main` have no `migrations/`. Latest per heavy app: accounts 0012, agents 0007, billing 0003, comments 0010, contacts 0005, crm 0005, inbound_email 0010, inbox_hub 0001, kanban 0004, knowledge 0005, notifications 0006, **tenants 0011 (untracked)**, tickets 0027.
> - **INSTALLED_APPS = 46** = 21 `apps.*` + `main` + 24 others (`daphne` + `jazzmin` at entries 1–2; 7× `django.contrib.*` incl. `postgres`; 15 third-party incl. drf, simplejwt, channels, allauth ×6, whitenoise, etc.). **`apps.nav` is NOT installed** — URL-only module mounted at `/api/v1/nav/`.
> - **MIDDLEWARE = 14 layers** (4 custom: SessionVersionMiddleware, TenantMiddleware, SubscriptionMiddleware, RateLimitHeadersMiddleware).
> - **29 `path()` in `main/urls.py`** (incl. authed `media/<path:path>`); **23 `/api/v1/*` includes** (22 unique URLConfs — `inbound-email/` dual-mounts as `emails/`). **36 frontend routes** in `apps/tenants/frontend_urls.py` (includes the legacy `/inbox-hub/`→`/emails/` redirect).
> - **6 WebSocket consumers** (`main/asgi.py`). `apps/inbox_hub/routing.py` is the **RoutingEngine** (email→department), NOT a Channels route.
> - **27 Celery `@shared_task`** across 10 task modules; **12** in Beat (15 unscheduled).
> - **43 signal receivers** across 10 apps with `signals.py` + `notifications/signal_handlers.py`. **`apps/inbox_hub` has NO `signals.py`** and `apps.py` has no `ready()` — it fans events imperatively.
> - **NotificationType 21 / INTERNAL_ONLY_TYPES 6 / ActivityLog action 34 / TicketActivity Event 27 / HubEmail 9 states + 4 priorities (no "medium") / InboundEmail.Status 9 / Reminder.Priority 4 (HAS "medium").**
> - **78 test modules** (68 root `tests/test_*.py` + 10 app-level). conftest 343 LOC. pytest collects **1071 items**.
> - **`static/css/custom-v15.css` = 25,225 LOC on disk** (committed HEAD = 25,149; +76 net uncommitted — dashboard `.activity-badge` + cockpit `.ih-assignee-chip` styles). `custom.css` = 20,431 LOC (committed snapshot, NOT loaded, theme-check-allowlisted).
> - **14 JS files / 5,919 LOC** in `static/js/` (`inbox-hub.js` **1,441** `?v=13`, `app.js` **1,273**). **Only `inbox-hub.js` is cache-busted**; the other 13 are unversioned (an uncommitted `app.js` change can serve stale without a hard refresh).
> - **48 `.html` templates**; **18 `pages/` subfolders**; **8** `pages/` root files; **6 `includes/`** (1 orphan: `kb_sidebar_widget.html`; `page_back_button.html` included by 18).
> - **TicketViewSet = 31 `@action`s** (DRF `mapping`); **HubEmailViewSet = 10 actions** (+ 4 config viewsets). **8 management commands.** Makefile ~24 `.PHONY` targets.

## Project Overview

Multi-tenant CRM, Ticketing, Knowledge Base and VoIP SaaS. **Django 6.0.2 + DRF 3.16+ + Channels 4.2+ + Celery 5.4+** with Bootstrap 5.3.3 + vanilla JS frontend (SIP.js softphone, TipTap rich editor, DOMPurify sanitization). Row-level multi-tenancy via subdomain routing and **contextvars-based** tenant binding (async-safe). Admin is jazzmin-skinned, superuser-locked. PM2 process management. **CI exists** — `.github/workflows/ci.yml` runs ruff (non-blocking) + migrate-check + theme-check + `pytest` against **PostgreSQL 16 + Redis 7** on every push-to-`main`/PR; locally `make check` (lint → migrate-check → test) is the same gate.

**Port:** 8001 (ASGI via Gunicorn + Uvicorn worker) | **Dev DB:** SQLite | **Prod DB:** PostgreSQL
**Redis:** db3 (cache + `cached_db` sessions, prefix `kanzan`), db4 (Celery broker; result backend = `django-db`), db5 (Channels layer, prefix `kanzan:channels`)
**SMTP in-process server:** 2525 (kanzan-smtp PM2 process, prod only) | **Flower:** 5556 | **TIME_ZONE:** `Asia/Kuala_Lumpur` (⚠ `CELERY_TIMEZONE = "UTC"` — crontab beat entries fire on UTC wall-clock; `USE_TZ=True`)

## Quick Reference

```
Superuser:      admin@kanzen.local / Pl@nC-ICT_2024
Django Admin:   http://localhost:8001/admin/   (jazzmin "darkly"; locked to is_superuser — see main/admin.py)
Tenants:        http://straat-x.localhost:8001
Flower:         http://localhost:5556 (admin:changeme — KANZAN_FLOWER_AUTH, read only by PM2 configs)
API Docs:       http://straat-x.localhost:8001/api/docs/
```

## Project Structure

```
/home/kavin/Kanzen/
├── apps/                          # 22 dirs (excl __pycache__); 21 in INSTALLED_APPS (nav is URL-only, no models.py)
│   ├── accounts/                  # Users (+is_service_account), 7-role RBAC + temp-role overrides + temp-perms intersection, invitations, profiles, UserGroups, middleware
│   ├── agents/                    # AgentAvailability (+last_seen presence heartbeat, is_assignable gate) + CustomAgentStatus + presence.py + reap-stale-presence task + load-fairness pick_email_agent (⚠ raw role @226)
│   ├── analytics/                 # Reports, dashboard widgets (⚠ DashboardView IsAuthenticated-only), exports (PDF/XLSX→CSV placeholder), calendar events
│   ├── api_keys/                  # APIKey (SHA-512) + auth class + viewset + per-key throttle + rate-limit-headers middleware + drf-spectacular extension
│   ├── attachments/               # File uploads (polymorphic GenericFK, python-magic MIME, 25MB cap); access.py (object-level authz) + media_views.py (authed /media/ serving)
│   ├── billing/                   # Stripe billing, plans, subscriptions, webhooks (5 events + replay guard), decorators (⚠ require_feature dead; NO tasks.py)
│   ├── comments/                  # Comment + Mention + CommentRead + ActivityLog (34 actions incl. 8 EMAIL_*) + LIVE signals (✅ internal-comment bodies REDACTED on wire)
│   ├── contacts/                  # Contacts, Companies, Accounts, Groups, ContactEvent (360°, NOT live-broadcast) + LIVE signals + context.py (build_contact_context, shared with Hub cockpit)
│   ├── crm/                       # Activity + Reminder (M2M contacts/tickets; +due_notified_at) + lead/account scoring + LIVE signals + fire_due_reminders task (Feature A)
│   ├── custom_fields/             # EAV custom fields per tenant + sync signals (✅ Ticket + Contact + Company)
│   ├── inbound_email/             # SMTP+IMAP ingestion; forks on TenantSettings.inbox_hub_enabled → legacy ticket-create OR park in Hub; agent email-inbox handoff; create_ticket+overrides (Feature B); ticket_overrides.py (shared validator) + attachments.py (serialize/stream customer files)
│   ├── inbox_hub/                 # Email-triage workspace: 8 models + services + RoutingEngine + AssignmentEngine + state machine + SLA task + 10 viewset actions (+context +attachment) + 4 config viewsets + access.py (DEPARTMENT-scoped). NO signals.py.
│   ├── kanban/                    # Visual boards, columns (is_personal), polymorphic CardPosition; cross-status drags route through tickets service (full audit/feed/SLA)
│   ├── knowledge/                 # KB articles (PG FTS), categories, search, stale alerts, gap digest, allowed_groups M2M (⚠ KBRevision/KBTicketLink dead-write)
│   ├── messaging/                 # Real-time conversations (WS); Conversation.source_group; attachments on messages (POST broadcast action); ✅ cross-tenant user scoping
│   ├── nav/                       # URL-only module (BadgeCountView — 7 categories; effective_role; NOT an installed app)
│   ├── newsfeed/                  # Internal announcements, reactions, read receipts + LIVE signals (draft-broadcast suppressed)
│   ├── notes/                     # Personal sticky notes (6 colors, pinning) — no signals
│   ├── notifications/             # In-app + email + WebSocket notifications (21 NotificationType incl. 5 HUB_EMAIL_* + REMINDER_DUE); NOT polymorphic (data JSONField)
│   ├── tenants/                   # Tenant model, middleware, frontend views, frontend_urls (36 routes), live broadcast layer, palette; LiveEventConsumer stamps presence on heartbeat
│   ├── tickets/                   # Core ticketing; Queue gains optional department FK; access.py (shared agent-visibility helper — creator KEEPS ticket after handoff); SLA + business hours, CSAT, pipelines, macros, webhooks, deals
│   └── voip/                      # Asterisk ARI integration, SIP softphone, call logs, recordings, queues (runtime is manual-launch — see Pitfalls)
├── main/                          # Django project root
│   ├── settings/{__init__,base,dev,prod}.py  # __init__ branches on DJANGO_DEBUG (**default False** → dev.py else prod.py); base.py holds CELERY_BEAT_SCHEDULE (12) + AGENT_PRESENCE_* + HUB_SLA_*
│   ├── checks.py                  # system checks; kanzen.E001 hard-fails if DEBUG is True (deploy tag)
│   ├── admin.py                   # SuperuserOnlyAdminSite (reassigns admin.site.__class__) + TenantFilteredAdmin mixin; registers 0 models
│   ├── celery.py                  # Celery app + queue routing (8 globs incl. default) — NO beat schedule (lives in base.py)
│   ├── asgi.py                    # ProtocolTypeRouter: HTTP + WebSocket (6 consumer endpoints, WebSocketTenantMiddleware)
│   ├── context.py                 # contextvars-based tenant context (async-safe)
│   ├── models.py / managers.py    # TimestampedModel, TenantScopedModel; TenantQuerySet, TenantAwareManager (fail-closed .none()), SoftDeleteTenantManager
│   └── urls.py                    # 29 path() (23 /api/v1/ includes; 22 unique URLConfs) + /admin/ + /api/{schema,docs}/ + /accounts/ + authed media/ + frontend ""
├── templates/                     # 48 .html files (18 subfolders under pages/)
│   ├── base.html                  # 292 lines — palette <style>, toast container (hardcoded z-index:1090), live-bus + live-connection JS, Flatpickr loader, sidebar-collapse FOUC fix, #reminderDueModal
│   ├── includes/                  # 6 files — navbar, sidebar (Inbox section gated {% if can_access_inbox_hub %}), softphone, messages, page_back_button (18 includes), kb_sidebar_widget (ORPHAN)
│   ├── pages/                     # 18 subfolders + 8 root html files (403, api_quickstart, calendar, dashboard, landing, login, profile, register)
│   ├── landing/landing_crm.html   # Standalone marketing page (doesn't extend base.html)
│   └── {auth,knowledge,notifications,tickets}/email/  # transactional email templates
├── static/
│   ├── css/custom-v15.css         # 25,225 LOC on disk (loaded; "Design System v9.0 Crimson Black")
│   ├── css/custom.css             # 20,431 LOC (committed snapshot, NOT loaded, allowlisted in theme check)
│   ├── images/                    # Logo, favicon (DP.png), hero artwork
│   └── js/                        # 14 vanilla-JS modules (5,919 LOC, incl. live-bus + live-connection + inbox-hub.js 1,441 + app.js 1,273)
├── tests/                         # 68 root pytest modules + 10 app-level (78 total); 1071 collected
├── conftest.py / pytest.ini       # 343 LOC: 16 factories + 20 fixtures (3 autouse); pytest.ini = 3 lines, no asyncio_mode
├── requirements/{base,dev,prod}.txt   # prod = -r base.txt (no extras); dev = base + tools (incl. pytest-asyncio; ⚠ still no pytest-timeout)
├── .github/workflows/ci.yml       # CI: ruff(non-blk)+migrate-check+theme-check+pytest on PG16+Redis7
├── requirements.txt               # ROOT — byte-identical duplicate of requirements/base.txt
├── ecosystem.config.js            # PM2 prod: 5 processes (django, celery-worker, celery-beat, flower, smtp)
├── ecosystem.dev.config.js        # PM2 dev: 4 processes (no SMTP, watch-mode reloads)
├── Makefile                       # ~24 targets (logs-django in .PHONY but no rule body → calling it errors)
├── docs/                          # README + architecture.md (STALE) + reference/{4 docs, STALE} + ui-consistency-audit.md (stale) + deploy/protected-media.md + qa-audit-2026-06-14/ (11 files)
├── tmp/emails/                    # Dev email capture (filebased EmailBackend, gitignored)
├── logs/                          # PM2 log files (gitignored; no rotation)
├── media/                         # User-uploaded: tenants/{id}/… and inbound_emails/{id}/…
├── scripts/                       # check_theme.py + .theme_baseline.json (147 hex literals across 11 files)
├── db.sqlite3                     # Dev database (~12MB, gitignored)
├── celerybeat-schedule            # Celery Beat shelve file (built-in scheduler — django-celery-beat removed for Django 6 compat)
└── .env                           # ~26 keys (.env.example covers 16; ~21 read-but-undocumented vars)
```

## Branch & Working-Tree State (2026-06-23)

- **`main` @ `94468bc` == `origin/main`** (last commit synced/pushed). Sprint-0 critical-fixes merged via **GitHub PR #1 (`5fda34a`)**; `94468bc` is a follow-up flatpickr CSS fix. The side branch `qa/sprint-0-critical-fixes` still exists at `59f3417`. Other local branches (`feature/api-keys`, `land-inflight-work`, `theming-refactor`) are stale.
- **What `main` @ `94468bc` contains (all committed):** Sprint-0 QA/security hardening, the first CI workflow, the launch-fundamentals security pass (authed `/media/`, `main/checks.py` DEBUG enforcement, HSTS-preload), the QA-audit follow-up defect fixes, the Inbox-Hub department-access refactor, **Feature A** (reminder-due popup), **Feature B** (create-ticket overrides + the Hub convert panel), and inbound-email attachments.
- **Uncommitted (the in-flight batch — 20 modified + 1 untracked migration):** **Feature C** + the **Emails/Inbox rename** (+ cockpit assignee chip) + the **two behaviour reversals** (creator keeps tickets after handoff; `TICKET_ASSIGNED` no longer emailed). The on-disk `CLAUDE.md` itself is also modified (this refresh). Files: `apps/inbound_email/services.py`, `apps/inbox_hub/{assignment,services,tasks}.py`, `apps/nav/views.py`, `apps/notifications/services.py`, `apps/tenants/{frontend_urls,models}.py`, `apps/tickets/access.py`, `static/css/custom-v15.css`, `static/js/{app,inbox-hub}.js`, `templates/includes/{page_back_button,sidebar}.html`, `templates/pages/{dashboard,emails/list,inbox_hub/list,settings/tenant}.html`, `tests/{test_access_control,test_email}.py`, + untracked `apps/tenants/migrations/0011_*.py`.

## Feature C — Agent-Decides Ticket Confirmation Email + Instant Reminder Scheduler (UNCOMMITTED)

### C1 — `auto_send_ticket_created_email` default flips True → False
- **`apps/tenants/models.py`** — `TenantSettings.auto_send_ticket_created_email` default changed `True → False`; help-text now says "When False (the default), agents decide per ticket and send it manually."
- **mig `tenants/0011_auto_send_ticket_created_email_default_false` [UNTRACKED on disk, `git status ??`]** — `AlterField` (default False) + **`RunPython disable_auto_send`** that `.update()`s existing rows holding the old `True` default to `False` (forward-only; reverse = noop, to avoid silently re-enabling auto-send). Uses historical `apps.get_model("tenants","TenantSettings").objects`.
- **`apps/inbound_email/services.py::_create_ticket_from_email`** — the auto-send guard flipped from `if settings_obj is None or settings_obj.auto_send_ticket_created_email:` (send-by-default, even when settings missing) → **`if settings_obj is not None and settings_obj.auto_send_ticket_created_email:`** (send ONLY when the toggle is explicitly on). When off, it logs a note and does not `transaction.on_commit(...)` the confirmation task.
- **`templates/pages/settings/tenant.html`** — the `#autoSendCreatedEmailToggle` switch no longer renders `checked`.
- **`tests/test_email.py`** — `7.8b` (no auto-confirm by default; `send_ticket_created_email_task.delay` not called; ticket still created → `Status.TICKET_CREATED`) and `7.8c` (opted-in tenants still get it).

### C2 — `ReminderScheduler` (instant client-side reminder popup)
- **`static/js/app.js`** — a new `ReminderScheduler` IIFE alongside `ReminderAlerts`, started from the DOM-ready handler when `#notifDropdown` is present. It fetches the user's upcoming pending reminders (`GET /api/v1/crm/reminders/?mine=true&status=pending&page_size=200`), arms a precise `setTimeout` per reminder within a **6-minute horizon** (re-syncs every **5 min** and on `reminder.*` / `live.reconnected` LiveBus events), and calls `ReminderAlerts.show()` at the *exact* `scheduled_at` so the popup feels instant. The server's `fire_due_reminders` (30s Beat) is now the **backstop** (closed tabs / other devices). Recipient rule mirrors the server: `assigned_to` if set, else `created_by`.
- **`ReminderAlerts.show()` dedup** — keyed on `reminder_id@scheduled_at_epoch_ms` (6h memory bound) so the instant timer and the later WS notification never double-pop; a rescheduled reminder (new due-time) can still re-alert.
- **`window.Toast = Toast` published** at the end of `app.js` — `const Toast` lived only in module lexical scope, so other scripts feature-detecting `window.Toast` (inbox-hub.js, the Emails page) silently fell back to `console.log`/`alert`. Now surfaced everywhere. (`inbox-hub.js` defensively accepts `window.Toast || Toast`.)

## Emails / Inbox Rename + Cockpit Assignee Chip (UNCOMMITTED)

The two email surfaces were **relabelled and their frontend routes swapped**:
1. The **Inbox Hub** triage cockpit is now labelled **"Emails"** at **`/emails/`** (was `/inbox-hub/`; URL name `inbox-hub`→`emails`; legacy `/inbox-hub/` now **302-redirects** to `/emails/` via a `RedirectView`, name `inbox-hub-redirect`). Sidebar icon = `ti-mail`.
2. The old personal **"Emails"** page is now labelled **"Inbox"** at **`/inbox/`** (was `/emails/`; URL name `emails`→`inbox`). Sidebar icon = `ti-inbox`.

**Kept as-is (intentional name↔label mismatch):** view funcs (`inbox_hub_page` serves `/emails/`; `emails_page` serves `/inbox/`), template dirs (`pages/inbox_hub/`, `pages/emails/`), `inbox-hub.js`, the `inbox_hub` Django app, DOM/badge ids, and the **`/api/v1/inbox-hub/` API** (NOT renamed — `/api/v1/emails/` is already the inbound-email alias mount, so renaming would collide). The sidebar section header stays **"Inbox"**.

**Notification click-through URLs updated to match:** Hub triage notifications (`assign`/`hold`/`escalate`/SLA in `inbox_hub/assignment.py`, `inbox_hub/services.py::_notify_escalation`, `inbox_hub/tasks.py`) → **`/emails/`**; the personal-inbox handoff (`inbox_hub/services.py::_notify_reassignment`) → **`/inbox/`**. `page_back_button.html` and `nav/views.py` docstrings updated. **`inbox-hub.js` bumped `?v=10`→`?v=13`.**

**Cockpit assignee chip (NEW feature, not just a rename):** `inbox-hub.js` + `templates/pages/inbox_hub/list.html` + `custom-v15.css` add an **"Assigned to X / you / Unassigned"** indicator both per-row (`.ih-row-assignee`) and in the detail action bar (`#ihDetailAssignee` / `.ih-assignee-chip`). Previously a successful Assign looked like a no-op (the row left the lens, the pane showed nothing). The dashboard recent-activity rows were also restructured (`.activity-header` + `.activity-badge` with a `--act-status` CSS var instead of an inline `background-color`).

## Behaviour Reversals (UNCOMMITTED — the historical docs describe the OPPOSITE)

### Agent ticket visibility — creator now KEEPS a ticket after handoff
- **`apps/tickets/access.py`** — `agent_visible_tickets_q(user)` is now `Q(assignee=user) | Q(created_by=user)` (was `Q(assignee=user) | (Q(created_by=user) & Q(assignee__isnull=True))`). `agent_can_see_ticket` is the OR equivalent. **A creator keeps the ticket in view even after assigning it to a different agent** — e.g. a triager who converts an inbound email and routes it to a teammate still finds it in their own list and detail. Admin/Manager (≤20) still bypass. Imported by `tickets/views.py`, `accounts/permissions.py` (`IsTicketAccessible`), `kanban/serializers.py`, `nav/views.py`, `analytics/services.py`, `attachments/access.py`.
- **`tests/test_access_control.py`** — `12.3b` inverted: `test_agent_created_ticket_visible_after_assigned_to_other` now asserts the creator STILL sees it in list + detail (200), and the new assignee sees it too.

### `TICKET_ASSIGNED` no longer emailed
- **`apps/notifications/services.py`** — `INTERNAL_ONLY_TYPES` gained `NotificationType.TICKET_ASSIGNED` (now **6 members**: TICKET_ASSIGNED, TICKET_OVERDUE, TICKET_FOLLOWUP_OVERDUE, REMINDER_DUE, REMINDER_OVERDUE, AGENT_STATUS_CHANGE). Ticket-assignment notifications now deliver **in-app/WS only** (`deliver_email` forced False), since they already surface as a live in-app notification — the emailed copy was redundant noise.

## Sprint 0 Hardening (MERGED into `main`)

A 2026-06-14 end-to-end QA/security audit (`docs/qa-audit-2026-06-14/`, readiness **38/100** pre-fix) found **6 confirmed Criticals + no CI**. All 8 Sprint-0 launch-blockers are implemented and on `main`:

1. **DEBUG split-default → fixed + hardened.** `main/settings/__init__.py` defaults `DJANGO_DEBUG=False` → an unset var fails safe to `prod.py`; `main/checks.py::kanzen.E001` hard-fails the deploy check if `DEBUG is True`. CI sets `DJANGO_DEBUG="True"` to keep tests on dev.py.
2. **Messaging cross-tenant enumeration/injection → fixed (4 sites)** — user resolution membership-scoped in `mentions.py`, `consumers.py`, `views.py`, `ConversationCreateSerializer.validate`. Tests: `tests/test_messaging_tenant_isolation.py`.
3. **Attachment authz → fixed** — `attachments/access.py::can_access_target` + `CanAccessAttachmentObject` perm + authed `download/` action; raw `/media/` authed via `media_views.py::serve_protected_media`. Tests: `tests/test_attachment_authz.py`.
4. **Billing VoIP entitlement → fixed** — `seed_plans.py` per-plan flags; data mig `billing/0003`; `check_call_limit` permits Pro/Enterprise.
5. **Billing re-subscribe IntegrityError → fixed** — `webhooks.py::_sync_subscription_from_stripe` repoints the existing OneToOne row.
6. **IMAP cross-tenant dedup → fixed** — `imap_poller.py::_ingest_one` resolves tenant first, dedups on `filter(tenant, message_id)` (`imap_poller.py:362`).
7. **CI → added** (`.github/workflows/ci.yml`).
8. **Stale tests re-greened.**

> **Launch-fundamentals + QA-audit-followup passes (also on `main`):** API-register email-verify bypass closed; HSTS-preload; messaging add-participant tenant-None deny; Hub reassign terminal-guard; newsfeed draft-broadcast suppressed; **Company custom-field sync added**; Stripe webhook replay guard; attachment download/validator 404/400 (not 500); 3 unwired webhook events fired; **message-edit silent-no-op fixed**; comment-mention tenant leak; inbound FAILED rollback; ticket-create assignee authz; grant-temp-role admin block; dashboard manager-analytics gate; **internal-comment LiveBus redaction**; **Hub `first_responded_at` stamping**; escalate terminal no-op; partial `effective_role` drift cleanup.

## Multi-Tenancy Architecture

### Three-Layer Isolation

1. **TenantMiddleware** (`apps/tenants/middleware.py`): Resolves tenant from subdomain (`{slug}.localhost` / `{slug}.{BASE_DOMAIN}`), or `TenantSettings.domain` for custom domains. `_extract_slug` rejects nested sub-subdomains. Sets `request.tenant` and binds context. **`EXEMPT_PATH_PREFIXES` (17 entries):** `/static/`, `/media/`, `/api/v1/accounts/auth/`, `/api/v1/billing/plans/`, `/api/v1/billing/webhook/`, `/api/v1/tickets/csat/`, `/api/docs/`, `/api/schema/`, `/accounts/`, `/inbound/email/` (⚠ dead — no URLConf), `/login/`, `/register/`, `/logout/`, `/verify-email/`, `/verify-email-sent/`, `/setup-company/`, `/workspaces/`. **`/admin/` is NOT exempt** — a dedicated branch resolves the tenant (even to None) and defers access control to `SuperuserOnlyAdminSite`. A real-host lookup that resolves to no tenant → `JsonResponse(404)`. **`/auth/handoff/` intentionally NOT exempt.**

2. **TenantAwareManager** (`main/managers.py`): Default `objects` auto-filters by `get_current_tenant()`. Returns an **empty queryset** (`.none()`) when no tenant in context (fail-closed). Use `Model.unscoped` for cross-tenant queries. `SoftDeleteTenantManager` adds `is_deleted=False`.

3. **TenantScopedModel** (`main/models.py`): Abstract base. UUID PK + Timestamped + `tenant` FK (CASCADE, `editable=False`, db_index=True). `objects = TenantAwareManager()`, `unscoped = models.Manager()`. Overridden `save()` auto-assigns `tenant` from context; **raises `ValueError`** if no tenant bound and none provided. `TimestampedModel` = UUID PK + `created_at` + `updated_at`, default ordering `["-created_at"]`.

### Async-Safe Tenant Context (`main/context.py`)

```python
set_current_tenant(tenant); get_current_tenant(); clear_current_tenant()  # clear hard-sets None
with tenant_context(tenant): ...  # snapshots+restores previous (nesting-safe — preferred for tasks/consumers)
```
A single `contextvars.ContextVar("current_tenant", default=None)`. **`clear_current_tenant()` is NOT nesting-safe**; middleware uses it in `finally` (fine per-request). `WebSocketTenantMiddleware` resolves tenant from Host, sets `scope["tenant"]`, binds, clears in `finally`.

### Admin: jazzmin theme + Superuser Lock + Tenant-Filtered Mixin (`main/admin.py`)

- **`daphne`/`jazzmin` are INSTALLED_APPS entries 1–2.** Admin is jazzmin "darkly"-themed; `SuperuserOnlyAdminSite` reassigns `admin.site.__class__` at import (`has_permission` = `is_active AND is_superuser`). jazzmin does NOT override `has_permission`, so the lock holds.
- **`TenantFilteredAdmin` mixin** — `get_queryset` uses `model.unscoped.all()` filtered by `request.tenant`; `get_form` injects a `tenant` ModelChoiceField; `save_model` backfills `obj.tenant`. **`main/admin.py` registers NO models** — registration happens per-app.

### Palette (`apps/tenants/colors.py::derive_palette`)

`derive_palette(primary, accent)` returns a ~21-key dict of CSS custom-property values (50–900 lightness scale via `colorsys` HLS, hover/active/dark/light/subtle/ring/rgb + WCAG-picked `text_on_primary`/`text_on_accent`; `logger.warning` if primary contrast < AA 4.5). **Defaults `#C1121F`/`#E11D2D`** (Crimson Black). Model field defaults `#6366F1`/`#F59E0B`. Wired via `context_processors.py` → `tenant_palette` → base.html `:root`.

## Settings Split, Redis, Ports

- `main/settings/__init__.py`: `from .base import *`, then `if env.bool("DJANGO_DEBUG", default=False)` → `from .dev import *` (try/except ImportError) **else** `from .prod import *`. Default fails safe to `prod.py`. **`main/checks.py::kanzen.E001`** hard-fails a deploy check if `DEBUG is True`.
- **Redis:** db3 cache + `cached_db` sessions (`KEY_PREFIX="kanzan"`); db4 Celery broker (result backend = `django-db`); db5 Channels (`prefix="kanzan:channels"`).
- **Sessions** `cached_db`, **host-only cookies** (no `Domain`); cross-host auth uses signed handoff tokens via `/auth/handoff/`.
- DRF auth order **JWT → APIKey → Session**; SimpleJWT access 15min/refresh 7d (rotate+blacklist, HS256, `JWT_SECRET_KEY` → falls back to `SECRET_KEY`). Allauth `ACCOUNT_LOGIN_METHODS={"email"}`; `django.contrib.sites` NOT installed. File upload caps 25MB. `django.contrib.postgres` installed (KB FTS).
- Prod media: `USE_X_ACCEL_REDIRECT` + `X_ACCEL_MEDIA_PREFIX` drive the `X-Accel-Redirect` path in `media_views.py`.

## Live Broadcast Layer

Unified pub/sub real-time layer: a per-tenant WebSocket fans server-side mutations to a client-side `LiveBus`. Coexists with per-domain consumers (chat, notifications, ticket-feed, presence, voip).

### Backend

- **`apps/tenants/live.py::broadcast_live_event(tenant, event, payload=None, *, immediate=False)`** — `tenant` may be a model instance OR a raw pk. Group: `live_tenant_{pk}`. Wire shape: `{type:"live_event", event:"<domain>.<verb>", payload:{...}, ts:ISO8601}`. Defers via `transaction.on_commit` (unless `immediate=True`); swallows `_send` exceptions (best-effort, logs).
- **`apps/tenants/consumers.py::LiveEventConsumer`** (group `live_tenant_{tenant_id}`). Anonymous/no-tenant → 4001; non-member → 4003. On `connect` stamps presence (`is_connect=True`); on `{action:"ping"}` re-stamps and replies `{type:"live.pong"}`. Presence NOT cleared on `disconnect` (reaper ages it out). The `live_event` handler re-shapes to `{type:<event name>, payload, ts}`.
- **`apps/tenants/routing.py`** → `re_path(r"ws/live/$", LiveEventConsumer.as_asgi())`.

### Signal Emitters

| App / source | Trigger | Verbs |
|-----|-----------|-------|
| `accounts` (`signals.py`, 5 receivers) | `TenantMembership.post_save/delete`, `Profile.post_save`, `User.post_save` (fans across active memberships) | `membership.created/updated/deleted`, `profile.created/updated`, `user.updated` |
| `comments` (2) | `Comment.post_save/delete` | `comment.created/updated/deleted` (✅ **internal `body` is `None` on the wire**) |
| `contacts` (8) | `Contact/Company/Account/ContactGroup × post_save/delete` (ContactEvent skipped; `last_activity_at` via `.update()` → no signal) | `contact.*`, `company.*`, `account.*`, `contact_group.*` |
| `crm` (4) | `Activity/Reminder × post_save/delete`. Reminder verb resolved by state (no `rescheduled` verb → reschedule emits `updated`) | `activity.*`, `reminder.created/updated/completed/cancelled/deleted` |
| `newsfeed` (4) | `NewsPost.post_save/delete`, `NewsPostReaction.post_save/delete` (draft-broadcast suppressed) | `newsfeed.created/updated/deleted`, `newsfeed.reacted` |
| `inbox_hub` (imperative — service/routing/assignment, NO signals.py) | park/route/assign/transition/escalate/reassign/convert/dismiss | `hub_email.created/transitioned/assigned/reassigned/escalated/converted_to_ticket/dismissed` |
| `agents`/`tenants` (presence) | `presence.handle_live_heartbeat`, `reap_stale_presence` task | `agent.presence` (broadcast `immediate=True`) |
| `crm` **task** (Feature A) | `fire_due_reminders` per due reminder | **`reminder.due`** (imperative, tenant-wide, payload incl. `recipient_id`) |

App configs (`apps/{accounts,comments,contacts,crm,newsfeed,tenants,tickets,voip,knowledge,custom_fields}/apps.py` + `notifications/signal_handlers.py`) import `signals` in `ready()` — **43 receivers total**. **`apps/inbox_hub/apps.py` has NO `ready()`**; no `signals.py`. **Tickets do NOT server-side broadcast to `live_tenant_*`** — `tickets/services.py::broadcast_ticket_event` publishes only to `ticket_feed_{tenant_id}`; the LiveBus bridge is **client-side** in `static/js/ticket-feed.js`.

### Frontend

- **`static/js/live-bus.js`** (175 LOC) — global `window.LiveBus`. API: `on/onMany/publish/debounce/rafBatch/isConnected/setChannelState`. Wildcard `"*"` gets all. Cross-tab fan-out via `BroadcastChannel('kanzan-live')`.
- **`static/js/live-connection.js`** (206 LOC) — global `window.LiveConnection`. Single shared `wss?://host/ws/live/`. Skips pre-auth pages / pages without a `sessionid` cookie. Backoff 1s→30s ±20% jitter, **infinite**. **25s heartbeat `{action:"ping"}`** with 8s pong timeout (drives server presence stamping). Publishes `live.reconnected` on reconnect; aggressive reconnect on tab visibility.
- **Wiring (`templates/base.html`)** — always: Bootstrap → DOMPurify → live-bus.js → api.js → app.js → command-palette.js → custom-select.js; then conditional on `tenant and user.is_authenticated`: live-connection.js → agent-availability.js → notes-panel.js → keyboard-shortcuts.js → ticket-feed.js → (if `voip_enabled`) SIP.js CDN + voip-softphone.js. **`inbox-hub.js` (`?v=13`) and `rich-editor.js` are page-specific** via `{% block extra_js %}`. `theme.js` loads synchronously in `<head>`. **Only `inbox-hub.js` is versioned.**

### Channel-Layer Groups & Close Codes

- `live_tenant_{tenant_id}`; `notifications_{user_id}`; `chat_{conversation_id}`; `ticket_feed_{tenant_id}`; `ticket_{ticket_id}_presence`; `voip_{tenant_id}`.
- `1000` clean; `4001` anon/no-tenant; `4002` invalid conversation UUID (chat); `4003` non-member; `4004` conversation tenant ≠ Host (chat). **`NotificationConsumer` and `CallEventConsumer` use a bare `close()`** for rejections.

## Feature A — Reminder-Due Popup (on `main`)

A "can't-miss" alert when a reminder comes due: a centered modal + Web-Audio chime + desktop/OS notification, delivered over `/ws/notifications/`. The uncommitted **Feature C** `ReminderScheduler` adds an exact-time client-side trigger on top.

### Data model + migration
- **`Reminder.due_notified_at`** (`apps/crm/models.py`, `DateTimeField(null, blank)`) — a **watermark, not a boolean**. Not indexed.
- **mig `crm/0005_reminder_due_notified_at`** — `AddField` + `RunPython suppress_existing_due_reminders` (reverse=noop): backfills `due_notified_at = F("scheduled_at")` for already-past-due AND active reminders so the first 30s tick does NOT pop the historical backlog. Uses `Reminder.objects` (historical manager has no `.unscoped`).

### Notification type
- **`NotificationType.REMINDER_DUE`** → NotificationType **21 members**. In **`INTERNAL_ONLY_TYPES`** (in-app/WS only, never emailed; now 6 members incl. TICKET_ASSIGNED). Reuses generic `send_notification(...)`.

### The task — `apps/crm/tasks.py::fire_due_reminders` (Beat `fire-due-reminders` @ 30s → kanzan_default)
- `@shared_task(bind, max_retries=1, acks_late)`. Per-tenant + per-row try/except; `Reminder.unscoped`.
- **Fire condition:** `scheduled_at <= now AND completed_at IS NULL AND cancelled_at IS NULL AND (due_notified_at IS NULL OR due_notified_at < F("scheduled_at"))` — the `< scheduled_at` half auto-re-arms after a forward reschedule.
- **Recipient** = `assigned_to or created_by` (`created_by` PROTECT → always a recipient).
- **Claim-first:** stamps `Reminder.unscoped.filter(pk=).update(due_notified_at=now)` **BEFORE** `send_notification`, so a downstream failure costs at most one *missed* alert, never a duplicate. `.update()` avoids re-tripping `post_save`. ⚠ No `select_for_update` (single-beat avoids the SELECT-then-UPDATE race).
- Emits `send_notification(REMINDER_DUE, data={reminder_id, contact_ids, priority, scheduled_at, url:"/reminders/"})` over `notifications_{recipient_id}` AND a tenant-wide `broadcast_live_event(tenant, "reminder.due", {...recipient_id...})` (drives the reminders-list refetch).

### Frontend
- **`static/js/app.js::ReminderAlerts`** IIFE (push-driven): in `initNotifications`, `if (data.type === 'reminder_due') ReminderAlerts.show(data); else showFlyout(data)`. `show()`: arms audio + `Notification.requestPermission`; Web-Audio two-tone chime (880/1174.66 Hz); desktop `Notification` (`requireInteraction:true`, `tag:reminder-<id>`, click → `/reminders/`); serializes a centered `#reminderDueModal`; degrades to a sticky Toast on pre-auth pages. **Now dedups** by `reminder_id@scheduled_at_epoch_ms` (Feature C). ⚠ `reminder_due` does NOT bump the sidebar Reminders badge.
- **`static/js/app.js::ReminderScheduler`** (Feature C, uncommitted) — exact-time instant trigger (see §Feature C).
- **`templates/base.html`** `#reminderDueModal` — `data-bs-backdrop="static"`. **There is NO `reminder-due-modal.js`** — the popup is entirely app.js-driven.
- **`templates/pages/reminders/list.html`** adds `'reminder.due'` to the LiveBus refetch subscription (debounced 500ms). `94468bc` also fixed a phantom scroll gap from closed flatpickr calendars (gated to `.open,.inline`).
- **Tests** (`tests/test_recalls.py::TestFireDueReminders`): fires once, future excluded, completed/cancelled excluded, creator fallback, re-arm on move-forward, suppressed-when-already-notified, internal-only, claim-first-prevents-refire.

## Feature B — Create Ticket From Email With Field Overrides (on `main`)

Upgrades email→ticket conversion from a one-click POST to a full **override form**. Both email→ticket surfaces share one validator and carry the full 9-field override set.

- **`apps/inbound_email/ticket_overrides.py::build_ticket_overrides(data, tenant)`** — single source of truth. Validates → raises **DRF 400 (not 500)**: `subject` ≤255, `description` ≤20000, `priority` ∈ `Ticket.Priority.choices` (case-insensitive), `category` ≤100, `queue`/`status` tenant-scoped PK lookup (malformed UUID → 400), **`status` rejects `is_closed`**, `assignee` must be an active `TenantMembership`, `due_date` parsed + made-aware, `tags` each ≤50. Blank/absent fields omitted so email-derived defaults survive.
- **`apps/inbound_email/services.py::_create_ticket_from_email(..., *, overrides=None)`** — overrides folded into the **initial `Ticket(**kwargs)`** (NOT a second `save()`), so `initialize_sla` runs against the final priority. Preserves the `"email"` provenance tag (deduped). **`_maybe_auto_assign` is SKIPPED when an explicit assignee override is supplied.** `overrides=None` → byte-identical legacy path.
- **`apps/inbox_hub/services.py::convert_to_ticket(...)`** — signature widened to the full 9 fields; passes a drop-None `overrides` dict into `_create_ticket_from_email` (SLA seeded against the right priority). Actor becomes ticket `created_by`.
- **Both API actions call the shared validator:** `InboundEmailViewSet.create_ticket` (`POST /inbound-email/{id}/create-ticket/`, role ≤30, idempotent → 400 w/ `ticket_number`) and `HubEmailViewSet.convert_to_ticket` (`POST /inbox-hub/hub-emails/{id}/convert-to-ticket/`). `ConvertToTicketSerializer` is schema-only (OpenAPI docs).
- **`templates/pages/emails/list.html`** — `#createTicketModal` form (subject* / priority / queue / status / assignee / category / description); `loadTicketMeta()` lazy-loads 4 dropdown sources; idempotent-400 treated as success.
- **`templates/pages/inbox_hub/list.html` + `static/js/inbox-hub.js` (`?v=13`)** — the cockpit "Convert" action opens a full **`#ihConvertPanel` offcanvas** (TipTap description + tags + flatpickr due-date + queue/status/assignee/priority/category).
- **Tests** (`tests/test_inbox_hub.py`): `TestConvertToTicketParity` + `TestHubConvertOverrides`.

## Feature — Inbound-Email Attachments (on `main`)

Surfaces + safely streams customer-sent email attachments on BOTH the Emails page and the cockpit. Inbound files are saved to `default_storage` and described in **`InboundEmail.attachment_metadata`** (JSONField list of `{filename, content_type, size, storage_path}`).

- **`apps/inbound_email/attachments.py`** — `serialize_attachments(inbound, url_for)` → renderable rows `{index, filename, content_type, size, is_image, url}` (URL **index-addressed** via `url_for(index)` — raw storage path never exposed). `stream_attachment(inbound, raw_index, *, force_download=False)` → `FileResponse`; bad/out-of-range index or missing file → **`Http404`**. **Safe raster images** (`png/jpeg/jpg/gif/webp/bmp` — `INLINE_IMAGE_TYPES`) served inline; **everything else (incl. SVG) forced to download**, always with `X-Content-Type-Options: nosniff`.
- **Authed download `@action` on BOTH viewsets:** `InboundEmailViewSet.attachment` (`GET /inbound-email/{id}/attachment/?i=<n>[&dl=1]`) and `HubEmailViewSet.attachment`. Each runs `get_object()` first so the surface's own permission stack gates the stream.
- **Serializers:** list rows carry `has_attachments` + `attachment_count`; detail adds the full `attachments` array with authed per-index URLs.
- **Tests:** `tests/test_inbound_email.py::TestInboundEmailAttachments` + `tests/test_inbox_hub.py::TestHubEmailAttachments`.

## Inbox Hub (full Phase-1B engine + triage cockpit + DEPARTMENT-scoped access — on `main`)

Email-to-Queue triage workspace. Reshapes inbound flow so NEW messages land in a centralised Hub for agent triage instead of auto-creating tickets, then routes to a department, seeds SLA deadlines, and auto-assigns to an online agent. **Default: OFF** — the seam at `apps/inbound_email/services.py` forks on `TenantSettings.inbox_hub_enabled` (default `False`).

> **Backend vs frontend split:** the *backend* is the full engine (routing, presence-aware assignment, hold/drain, state machine, SLA breach task, **10 viewset actions** incl. claim/escalate/transition/note + read-only `context` + authed `attachment` stream). The *frontend* (`inbox-hub.js` **1,441 LOC** `?v=13`, labelled **"Emails"**) is a **triage cockpit** surfacing **convert (full offcanvas) / assign / dismiss** over the untriaged backlog (5 workload lenses) + per-email **customer-context card + attachment thumbnails + the new assignee chip** — it does NOT call claim/escalate/transition/note.
>
> **⚠️ Access is DEPARTMENT-SCOPED** (the binary `UserGroup` gate is GONE; `user_in_any_group` deleted). `apps/inbox_hub/access.py` is the single source of truth (all gates use **`effective_role`**):
> - **`can_access_inbox_hub(membership, *, user, tenant)`** — Admin/Manager (`≤20`) → **always**; Viewer (`>30`) → **never**; agent-tier (Team Lead/Agent/IT/HR, 21–30) → granted iff (a) member of ≥1 **active** `Department`, OR (b) the tenant has **zero active departments** (fall-open safety valve), OR (c) they have ≥1 `HubEmail` assigned (black-hole safety valve).
> - **Row visibility** — supervisors (≤20) see all; agent-tier scoped by twin helpers: `hub_rows_q(user, dept_ids)` = `(state=NEW AND (department∈dept_ids OR department IS NULL)) OR assignee=me`, plus object-level `agent_can_see_hub_email`.
> - **Lockout surfaces:** hidden sidebar entry + zeroed badge (`nav/views.py`) + 403 on the page (`_inbox_hub_access_required`) + 403 on the API (`HubEmailPermission` + `IsHubEmailAccessible`).
> - **Rollout note:** on a tenant that already has departments, an agent who is NOT a Department member (and has no assigned mail) **403s** — make triagers Department members.

### The seam — `apps/inbound_email/services.py` (`process_inbound_email`)

```python
if existing_ticket:
    _add_reply_to_ticket(inbound, existing_ticket, contact, system_user)   # ALWAYS — replies never go to the Hub
else:
    settings = getattr(tenant, "settings", None)        # ⚠ shadows django.conf.settings
    if settings is not None and settings.inbox_hub_enabled:
        from apps.inbox_hub.services import park_email_in_hub
        park_email_in_hub(inbound, tenant, contact, system_user)
    else:
        _create_ticket_from_email(inbound, tenant, contact, system_user)   # (Feature B adds overrides=; Feature C gates the confirmation email)
```
**Existing-thread replies always go straight to the matching ticket.** **Footgun:** the local `settings` rebinds the module-level `from django.conf import settings`.

### Models (8 — `apps/inbox_hub/models.py`, 1 migration `0001_initial`)

- **`Department`** — `name`, `slug` (unique per tenant), `lead` (FK User PROTECT), `members` (M2M via `DepartmentMembership`), `default_queue` (FK Queue SET_NULL), `business_hours` (FK SET_NULL), `is_active`.
- **`DepartmentMembership`** — through-model. `skills` (JSON — **seeded-but-unused**).
- **`HubEmail`** — workspace entity, 1:1 `InboundEmail`. **9-state enum** (`NEW → ASSIGNED → IN_PROGRESS → PENDING_AGENT ⇄ AWAITING_CUSTOMER → ESCALATED → RESOLVED → CONVERTED_TO_TICKET | DISMISSED`) + **4-priority enum** (`low/normal/high/urgent` — **NO "medium"**; contrast Reminder HAS medium). SLA fields (`sla_response_due_at` indexed, `sla_resolution_due_at`, `response_breached`, `resolution_breached`, `first_responded_at` ✅stamped by `transition`, `first_assigned_at`, `pause_started_at`/`total_pause_seconds` ⚠unused). Terminal: `converted_ticket` (1:1 Ticket SET_NULL), `dismissed_at`/`by`/`reason`. `auto_classification_data` JSONField. `tags` JSONField (**never written**). **5 indexes**.
- **`HubEmailAssignment`** — immutable audit. `Reason` enum (`AUTO/MANUAL/ESCALATION/REASSIGNMENT` — **ESCALATION never emitted**).
- **`HubEmailNote`** — internal note (`ordering=["created_at"]` ASC).
- **`HubEmailSLA`** — per-(queue, priority) or per-(department, priority) policy; `escalation_minutes` **unused**.
- **`RoutingRule`** — ordered IF/THEN. `match` JSON keys AND, values OR.
- **`QueueRouting`** — 1:1 supplement to `Queue`. `strategy_code` + `leave_unassigned_when_no_match` (**unused**).

### State machine (`state_machine.py`)

`can_transition(old,new)` False if equal; `assert_transition` raises `ValueError`. **`convert_to_ticket`, `dismiss_hub_email`, `assign_to`, `reassign_hub_email` set `state` directly (no `assert_transition`)**; only `transition_hub_email` enforces it.

### Engine

**`_post_park_hooks`** (on_commit, each step try/except-isolated): (1) `RoutingEngine.classify_and_route`, (2) `_initialize_hub_sla` (wall-clock deadlines), (3) `AssignmentEngine.try_assign` (only when `inbox_hub_auto_assign`, default True).

**`RoutingEngine`** (`routing.py`): `RoutingRule.unscoped.filter(tenant, is_active=True).order_by("order","id")`. Match keys AND / values OR; `sender_domain` exact-or-`.subdomain`; `recipient_local` exact; `keyword` substring (subject+body); `subject_regex` IGNORECASE (invalid → fail-closed + warn). **Empty `match` matches nothing.** Last-matched non-null outputs win; `stop_on_match` breaks. Fallback dept = `inbox_hub_default_department` (if active) else single active Department. Queue fallback = `department.default_queue`. Writes `EMAIL_CATEGORISED` (+`EMAIL_QUEUED`), broadcasts `hub_email.transitioned`.

**`AssignmentEngine`** (`assignment.py`): string-token strategies (`availability_aware → least_loaded → round_robin`). `_candidate_user_ids`: department members (or, if no department, `TenantMembership` at **`role__hierarchy_level == 30`** — ⚠ **raw role, not effective_role**, `assignment.py:228`), then `is_assignable`-filtered. `try_assign` — if none online → "held" + `_notify_hold` to the dept lead (url `/emails/`). `assign_to` — atomic `select_for_update`, concurrency guard, sets assignee/`first_assigned_at`/state→ASSIGNED, creates `HubEmailAssignment(AUTO)`, writes `EMAIL_AGENT_ASSIGNED`, broadcasts `hub_email.assigned`. **Does NOT touch `inbound.assignee`.** `drain_department_backlog` runs on agent (re)connect.

**Presence layer** (`apps/agents/presence.py` + `models.py` + `tasks.py`): `AgentAvailability.last_seen` (DateTime indexed, mig `agents/0007`). `DEFAULT_PRESENCE_TTL_SECONDS = 90`. **`is_assignable`** = `ONLINE` AND `remaining_capacity > 0` AND `presence_fresh` AND (if `auto_away_outside_hours`) within working hours (⚠ **server-local** time) — the single auto-assign gate. `touch_presence` get_or_create on `.unscoped`, auto-promotes OFFLINE→ONLINE only (gated by `AGENT_PRESENCE_AUTO_ONLINE`, default True). `handle_live_heartbeat` stamps + broadcasts `agent.presence` + drains backlog. `reap_stale_presence` (Beat 60s) flips stale ONLINE→AWAY.

**`apps/inbox_hub/tasks.py::check_hub_sla_breaches`** (Beat 120s; cross-tenant `.unscoped`) — sweeps active states (`NEW/ASSIGNED/IN_PROGRESS/PENDING_AGENT/ESCALATED`) with a response deadline. Flags response breaches (auto-escalates via `escalate_hub_email`), fires a one-shot warning `HUB_SLA_WARNING_MINUTES` (default 15) before deadline (deduped via `auto_classification_data["sla_warning_sent"]`), flags resolution breaches (flag-only). Notification url `/emails/`. **⚠ `first_responded_at` only stamped by `transition_hub_email` (which the cockpit never calls), so in the default cockpit flow the response-breach guard `first_responded_at is None` is usually True → the breach still fires + auto-escalates.**

### Service layer (`apps/inbox_hub/services.py`)

All write polymorphic `ActivityLog` rows + broadcast LiveBus on commit. **All 8 `EMAIL_*` ActivityLog actions + all 5 `HUB_EMAIL_*` Notifications emitted.**
- `park_email_in_hub` — idempotent `get_or_create(inbound=…)`; `EMAIL_RECEIVED`; broadcasts `hub_email.created` (immediate); schedules `_post_park_hooks`.
- `convert_to_ticket(...)` — idempotent; reuses `_create_ticket_from_email` (Feature B widened overrides); state→CONVERTED; `EMAIL_CONVERTED_TO_TICKET`. Actor becomes ticket `created_by`.
- `dismiss_hub_email(...)` — idempotent; state→DISMISSED; `EMAIL_DISMISSED`.
- `transition_hub_email(...)` — `assert_transition`; **stamps `first_responded_at` on entry into a RESPONSE_STATE** (`IN_PROGRESS/PENDING_AGENT/AWAITING_CUSTOMER/RESOLVED`); `STATUS_CHANGED`; `hub_email.transitioned`.
- `escalate_hub_email(...)` — `escalation_count += 1` **(bumped even when the ESCALATED transition is illegal — only the state change is gated)**, `escalated_to`=dept lead, state→ESCALATED only if legal; `EMAIL_ESCALATED`; `_notify_escalation` (url `/emails/`).
- `reassign_hub_email(...)` — `select_for_update`; online NOT required; rejects terminal states; `EMAIL_REASSIGNED`/`EMAIL_AGENT_ASSIGNED`. **Also stamps `inbound.assignee = new_user` + `inbox_status=PENDING` + `is_read=False`** — the agent email-inbox handoff backing `assign`/`reassign`/`claim`. `_notify_reassignment` url `/inbox/`.
- `add_hub_email_note(...)` — creates `HubEmailNote`; broadcasts `hub_email.transitioned`.

### API surface (`apps/inbox_hub/views.py` + `urls.py`)

`HubEmailViewSet` (list/retrieve only) permission stack `[IsAuthenticated, IsTenantMember, HubEmailPermission, IsHubEmailAccessible]`. **Agent-tier row filter (uses `effective_role`):** for `level > 20`, applies `hub_rows_q`. Chips: `assignee=me|unassigned|<uuid>`, `state`/`priority`/`queue`/`department`, **`sla_risk=true`**.

| Action | Method / URL | Codename | Service / note |
|---|---|---|---|
| `list`/`retrieve` | `GET /hub-emails/[{id}/]` | `hub_email.view` | — |
| **`context`** | `GET /{id}/context/` | `hub_email.view` | `contacts.context.build_contact_context` |
| **`attachment`** | `GET /{id}/attachment/?i=<n>[&dl=1]` | `hub_email.view` | `attachments.stream_attachment(obj.inbound, i)` |
| `convert_to_ticket` | `POST /{id}/convert-to-ticket/` | `hub_email.convert` | full 9-field override set via `build_ticket_overrides` |
| `dismiss` | `POST /{id}/dismiss/` | `hub_email.dismiss` | `dismiss_hub_email` |
| `assign` | `POST /{id}/assign/` | `hub_email.assign` | `reassign_hub_email` (validates member) |
| `reassign` | `POST /{id}/reassign/` | `hub_email.reassign` | `reassign_hub_email` |
| `claim` | `POST /{id}/claim/` | **none** — agent-level (≤30) | `reassign_hub_email(self)` |
| `escalate` | `POST /{id}/escalate/` | `hub_email.escalate` | `escalate_hub_email` |
| `transition` | `POST /{id}/transition/` | **none** — agent-level (≤30) | `transition_hub_email` (`ValueError`→400) |
| `note` | `POST /{id}/note/` | `hub_email.note` | `add_hub_email_note` → 201 |

**No `reply` action** despite the `hub_email.reply` codename. **`claim`/`escalate`/`transition`/`note` are live backend endpoints the cockpit never calls** (dead-but-present). **4 config viewsets:** `DepartmentViewSet` (+add/remove-members), `RoutingRuleViewSet` (+reorder, manager-gated), `HubEmailSLAViewSet`, `QueueRoutingViewSet`.

**`HubEmailPermission`** (`permissions.py`) — a **local** permission class (NOT the global `ACTION_MAP`). Uses `effective_role`. **First gate = `can_access_inbox_hub`**. Then `AGENT_LEVEL_ACTIONS = {claim, transition}` gated `≤30`; else per-action codename / hierarchy fallback: `view ≤40`, `convert/reply/escalate/note ≤30`, `assign/reassign/dismiss ≤20`. **`IsHubEmailAccessible`** delegates to `agent_can_see_hub_email`.

### RBAC (12 codenames — `apps/accounts/defaults.py` + mig `accounts/0012`) + department gate

`hub_email.{view, assign, reassign, convert, dismiss, reply, escalate, note}` (8) + `department.{view, manage}` (2) + `routing_rule.manage` (1) + `hub_sla.manage` (1). Grants: Admin/Manager = all 12; Team Lead = agent-tier + `assign/reassign/dismiss`; Agent/IT/HR = agent-tier; Viewer = `view` via ≤40 fallback. **The codename grants are SUBORDINATE to the department access gate.**

### Agent email-inbox handoff (`InboundEmail.assignee`)

- **`InboundEmail.assignee`** FK (SET_NULL, db_indexed, `related_name="assigned_inbound_emails"`, mig `inbound_email/0010`, **mutable**). **Set only by `reassign_hub_email`** (assign/reassign/claim). **`AssignmentEngine.assign_to` (auto-assign) does NOT touch it.**
- **`apps/inbound_email/api_views.py`**: `InboundEmailViewSet.get_queryset` query-param branches — `?assigned=me`, `?internal=true`, `?mine=true`; default hides BOUNCED. The **`create_ticket` action** (`get_permissions` swaps to `[IsAuthenticated, IsTenantMember]`; handler enforces `effective_role ≤ 30`; idempotent → 400): if `email.hub_email` exists → `convert_to_ticket`; else `_create_ticket_from_email` (both via Feature B overrides).
- **`templates/pages/emails/list.html`** (now labelled **"Inbox"** at `/inbox/`): "Assigned to me" tab; dual-source load (`?internal=true&mine=true` + `?assigned=me`).

### Configuration & seeding

- **`TenantSettings`** Hub fields: `inbox_hub_enabled` (default **False**), `inbox_hub_auto_assign` (default **True**), `inbox_hub_default_department` (FK SET_NULL).
- **Settings constants** (`base.py`, env-overridable): `AGENT_PRESENCE_TTL_SECONDS=90`, `AGENT_PRESENCE_AUTO_ONLINE=True`, `HUB_SLA_WARNING_MINUTES=15`.
- **`manage.py seed_inbox_hub_defaults [--tenant-slug <slug> | --all-tenants]`** — seeds one "General" Department (idempotent). **Does NOT seed** RoutingRules/HubEmailSLA/QueueRouting.

### Known gaps / footguns (Inbox Hub)

- **Auto-assign ≠ email handoff** — `assign_to` sets only `HubEmail.assignee`.
- **Dead-but-present surface** — backend `claim`/`escalate`/`transition`/`note` + `hub_email.reply` codename unused by the cockpit.
- **`first_responded_at` rarely written in practice** (only via `transition`) → response-breach usually fires on the deadline (+auto-escalates).
- **`escalate_hub_email` increments `escalation_count` even on illegal transition.** `Reason.ESCALATION` never emitted.
- **Seeded-but-unused fields**: `DepartmentMembership.skills`, `HubEmail.tags`/`pause_started_at`/`total_pause_seconds`, `QueueRouting.leave_unassigned_when_no_match`, `HubEmailSLA.escalation_minutes`.
- **`InboundEmail.Status.PARKED_IN_HUB` write-dead** — `park_email_in_hub` keeps `PROCESSING`.
- **`_candidate_user_ids` no-department fallback uses raw `role`, not `effective_role`** (`assignment.py:228`).
- Still future: business-hours-aware Hub SLA; `auto_classification_data` AI classification; historical backfill when the flag flips ON; auto-seed of a default Department; RoutingRule/HubEmailSLA/QueueRouting seeding.

## Models (91 model classes across 21 apps with models.py)

### Base Models (Abstract)
- **TimestampedModel**: UUID PK + `created_at` + `updated_at`; default ordering `["-created_at"]`.
- **TenantScopedModel**: TimestampedModel + `tenant` FK (CASCADE, editable=False, db_index=True) + auto-filtering.

### Tenants / Accounts

**tenants** (2): `Tenant` (name, slug unique, domain unique nullable, is_active, logo); `TenantSettings` (1:1; auth_method, SSO config, timezone, date_format, branding `#6366F1`/`#F59E0B` with hex validators, `inbound_email_address` unique, business hours/days, `auto_close_days` 5, `csat_delay_minutes` 60, `auto_transition_on_assign` True, **`auto_send_ticket_created_email` default False** [Feature C, was True], `auto_assign_inbound_email_tickets` default False, **`inbox_hub_enabled`** False, **`inbox_hub_auto_assign`** True, **`inbox_hub_default_department`** FK).

**accounts** (8): `User(AbstractUser)` (email-based, UUID PK, `username=None`, `auth_version`, `avatar`, `phone`, `is_service_account`). `Permission` — **global**; `Action` enum. ⚠ inbox-hub verbs (`convert/dismiss/reassign/reply/escalate/note`) NOT in `Permission.Action` but stored as the `action` value. `Role(TenantScopedModel)` — M2M `permissions`, `hierarchy_level` default 100, `is_system`. `Profile(TenantScopedModel)` (per-tenant prefs incl. free-text `department` string). `TenantMembership` — NOT TenantScoped; FKs `user`/`tenant`/`role` (PROTECT)/`temporary_role`/…; **M2M `temporary_permissions`**; methods `effective_role`, `get_effective_permissions_qs()`, `has_effective_permission()`. `Invitation`. `UserGroup` (M2M `members`; "one user per group" enforced; ⚠ NO LONGER gates the Hub — that's department-scoped now). `EmailVerificationToken` (global).

### Tickets (22)

`Pipeline`, `PipelineStage`, `TicketStatus` (`pauses_sla`, `is_closed`, `is_default`), `Queue` (`default_assignee`, `auto_assign`, **`department` FK → inbox_hub.Department SET_NULL**, mig 0027), `TicketCategory`, `TicketCounter` (NOT TenantScoped; `next_number()` SELECT-FOR-UPDATE — no-op on SQLite), `Ticket` (~80 fields; soft delete; CSAT; deal fields; `merged_into`; `pre_wait_status`; `tags`+`custom_data` JSON; **`save()` auto-populates `company_id`** from linked Contact; `clean()` validates assignee but **not** called by `save()`), `TicketLink` (4 types + circular guard), `SLAPolicy`, `EscalationRule`, `BusinessHours` (IANA tz + schedule JSON), `PublicHoliday`, `SLAPause`, `TicketActivity` (**27 `Event` choices** — inner class is `Event`, NOT `EventType`), `CannedResponse`, `Macro`, `SavedView`, `TicketAssignment`, `TicketWatcher`, `TimeEntry`, `TicketTemplate`, `Webhook` (HMAC SHA-256, 8 EventType, auto-disable at 10 failures).

### Contacts (5)

`Account` (`mrr`, `health_score` default 50, ⚠ clamped only in uncalled `clean()`), `Company` (name unique per tenant; `custom_data` JSON — ✅ **synced to CustomFieldValue** via `Company.post_save`), `Contact` (email unique per tenant, `email_bouncing` indexed, `lead_score` default 50 ⚠no clamp, `last_activity_at` written via `.update()` so no signal, `source` 6-choice), `ContactGroup` (M2M contacts), `ContactEvent` (append-only 360°; NOT live-broadcast; NOT in admin). `build_contact_context` in `context.py` (cache prefix `contact_context_v2`, 60s TTL) — shared by `ContactViewSet.context` and `HubEmailViewSet.context`.

### CRM (2)

`Activity` (call/email/meeting/task). `Reminder` (M2M `contacts`/`tickets`; `Priority` 4-choice incl. **MEDIUM** default — differs from HubEmail's no-medium; `status` is a derived property; custom `ReminderManager`/`ReminderUnscopedManager`; `mark_completed/mark_cancelled/reschedule`; **+ `due_notified_at`** watermark — Feature A).

### Inbound Email (3)

`InboundEmail` extends `TimestampedModel` (NOT TenantScopedModel — tenant nullable; default `objects` is a PLAIN manager). `Status` (**9 members** incl. `PARKED_IN_HUB` ⚠write-dead). **`assignee` FK** (SET_NULL, indexed, mutable, mig 0010). **`attachment_metadata` JSONField**. Threading: `message_id` (indexed, stored without `<>`), `in_reply_to`, `references`. Idempotency keys: `"in:{tenant}:{mid}"` / `"out:{tenant}:{ticket}:{mid}"` (unique). `save()` enforces immutability of `linked_at/by` + `actioned_at/by`. `BounceLog`, `IMAPPollState`. **Only InboundEmail in admin.**

### Knowledge (6)

`Category`, `Article` (status 5-choice; Postgres FTS via `SearchVectorField`+GinIndex — **dev no-op** on non-Postgres; **`allowed_groups` M2M to UserGroup**; **`tags` JSON IS live**; auto-slug; `save()` resolves tenant from context). `KBRevision` ⚠**dead-write**, `KBVote`, `KBSearchGap`, `KBTicketLink` ⚠**dead-write**. **Only Category + Article in admin.**

### Kanban (3)

`Board` (`resource_type` TICKET/DEAL, `is_default`, **`is_personal`**), `Column` (board, order, optional `status` FK, wip_limit), `CardPosition` (polymorphic GenericFK).

### Comments / Messaging / Newsfeed / Notifications

**comments** (4): `Comment` (polymorphic GenericFK, threaded, `is_internal`), `Mention`, `CommentRead`, `ActivityLog` (**34 action choices** — 26 core + 8 `EMAIL_*`). ✅ Comment broadcast **redacts internal bodies** (`body: None if is_internal`).

**messaging** (3): `Conversation` (DIRECT/GROUP/TICKET; FK `source_group`), `ConversationParticipant`, `Message` (`body` required at model level but blank-able in `MessageCreateSerializer`; null author = system; attachments via GenericFK). ✅ Cross-tenant user enumeration/injection fixed.

**newsfeed** (3): `NewsPost` (`author` CASCADE), `NewsPostReaction`, `NewsPostRead`.

**notifications** (2): `Notification` (**21 NotificationType** — +5 `HUB_EMAIL_*` + `REMINDER_DUE`). **NOT polymorphic** — `data` JSONField + `recipient` FK. `NotificationPreference`. Single creator `send_notification(...)`; `INTERNAL_ONLY_TYPES` (**now 6** incl. TICKET_ASSIGNED) force email off.

### Agents / Custom Fields / Billing / Analytics / Attachments / Notes / API Keys / VoIP / Inbox Hub

**agents** (2): `AgentAvailability` (+`last_seen`/`presence_fresh`/`is_assignable`/`is_available`/`remaining_capacity`); `CustomAgentStatus`.
**custom_fields** (2): `CustomFieldDefinition` (8 types × **3 modules: ticket/contact/company**), `CustomFieldValue` (EAV). ✅ Sync signals cover **Ticket + Contact + Company**.
**billing** (4): `Plan` (global; `has_voip`/`has_call_recording`/`max_calls_per_month` — ✅ seeder sets them; mig `billing/0003` backfills), `Subscription` (1:1 Tenant; ✅ webhook repoints on re-subscribe), `Invoice`, `UsageTracker`. **No `tasks.py`.** `require_feature` 100% dead. `SubscriptionMiddleware` → 402.
**analytics** (4): `ReportDefinition`, `DashboardWidget`, `ExportJob` (⚠ PDF/XLSX-without-openpyxl → CSV bytes), `CalendarEvent`. **⚠ `DashboardView` IsAuthenticated only.**
**attachments** (1): `Attachment` (polymorphic, **UUIDField object_id**, python-magic MIME, 25MB). ✅ `access.py` object-level authz + `media_views.py` authed `/media/`.
**notes** (1): `QuickNote`. **api_keys** (1): `APIKey` (SHA-512; `kz_live_<slug6>_<token_urlsafe(32)>`).
**voip** (5): `VoIPSettings` (singleton; `is_active` drives softphone UI, decoupled from `Plan.has_voip`), `Extension`, `CallLog`, `CallRecording`, `CallQueue`.
**inbox_hub** (8): see §Inbox Hub.

### Polymorphic (GenericFK) Models — 5 total

`Attachment`, `Comment`, `ActivityLog`, `CustomFieldValue`, `CardPosition`. **Not** Notification.

## Role-Based Access Control

**Hierarchy:** Admin(10) → Manager(20) → **Team Lead(25)** → Agent(30) / **IT(30)** / **HR(30)** → Viewer(40).

**Default role seeding** (`apps/tenants/signals.py::create_default_roles`) runs on `Tenant.post_save (created=True)`, seeding **all seven** system roles inline; permission sets for the six perm-bearing roles come from `apps/accounts/defaults.py::ROLE_DEFINITIONS` (**6 entries** — Viewer permission-less, leans on ≤40 fallback). `ALL_CODENAMES` = **69 unique codenames** (12 inbox-hub-related). `defaults.provision_default_roles` is **dead**; permissions seeded by migrations `accounts/0011`/`0012`.

- `is_admin` ≤10; `is_admin_or_manager` ≤20; `is_agent_or_above` ≤30. **Team Lead (25)** satisfies `is_agent_or_above` but NOT `is_admin_or_manager`. Viewer (40) → ≤40 view-fallback only.
- **Always use `TenantMembership.effective_role`** (temp role wins until expiry; `temporary_permissions` non-empty = intersection). ⚠️ **Confirmed raw-`role` drift (4 sites, 2026-06-23):** `agents/services.py:226` (`pick_email_agent`), `inbox_hub/assignment.py:228` (`_candidate_user_ids` no-dept fallback), `tickets/views.py:1052` (`teammates`) & `:1115` (`team_progress`) — raw `role__hierarchy_level`. A temp-promoted agent gets object-perms + badges as a manager but is still email-routed / list-filtered as their raw role.
- **`AgentAvailabilityViewSet.assignable_roles`** excludes the `admin` slug. `grant_temp_role` sets `temporary_permissions.set([...])`.
- **Shared visibility modules:**
  - **`apps/tickets/access.py`** — `agent_visible_tickets_q(user)` = **`Q(assignee=user) | Q(created_by=user)`** (UNCOMMITTED reversal: a creator KEEPS a ticket after handing it off — no longer requires `assignee IS NULL`) + object-level `agent_can_see_ticket`. Imported by `tickets/views.py`, `IsTicketAccessible`, `analytics/services.py`, `nav/views.py`, `kanban/serializers.py`, `attachments/access.py`. Admin/Manager (≤20) bypass.
  - **`apps/inbox_hub/access.py`** — DEPARTMENT-scoped Hub gate (see §Inbox Hub).
  - **`apps/contacts/context.py`** — `build_contact_context` (cache `contact_context_v2`, 60s TTL).
- **Permission classes** (`apps/accounts/permissions.py`):
  - `HasTenantPermission` — codename-based; `ACTION_MAP` maps ~95 DRF actions to `{resource}.{action}`. Uses `effective_role`; no `permission_resource` → **allow**; unmapped → **deny**. Explicit-perm first, then hierarchy fallback (`view ≤40`, `create/update ≤30`, else ≤20).
  - `IsTicketAccessible` (object-level; ≤20 bypass).
  - `IsTenantMember` (⚠ returns `True` when `request.tenant is None`), `IsTenantAdmin`, `IsTenantAdminOrManager`.
  - **`HubEmailPermission` + `IsHubEmailAccessible`** — Inbox Hub's local stack.
- `_role_required(20)` gates admin/manager pages; `_role_required(30)` gates the personal Inbox. **`/settings/`** is `@_membership_required + @ensure_csrf_cookie` — any member loads; API enforces admin-only writes.

## Signals (10 apps with signals.py + notifications/signal_handlers.py — 43 receivers)

- **Tenants — 2:** `Tenant.post_save(created)` → `create_tenant_settings` + `create_default_roles`.
- **Accounts — 5:** `TenantMembership.post_save/delete`, `Profile.post_save`, `User.post_save`.
- **Tickets — 11:** `pre_save handle_ticket_status_change`; `post_save` × `fire_ticket_created_signal`, `fire_ticket_assigned_signal`, `log_ticket_activity` (**2-sec dedup**), `handle_sla_pause_on_status_change`, `create_kanban_card_on_ticket_save`, `sync_kanban_card_on_status_change`, `sync_kanban_card_on_pipeline_stage_change` (⚠ by column **name**); `post_delete remove_kanban_cards_on_ticket_delete`; `@receiver(ticket_closed) check_kb_article_coverage`; `SLAPolicy.post_save propagate_sla_policy_change`.
- **Custom Fields — 3:** `Ticket/Contact/Company.post_save` → sync `CustomFieldValue`.
- **Knowledge — 1:** `Article.post_save update_search_vector` (PG FTS; `.update()`; non-Postgres no-op).
- **Notifications — 2 (`signal_handlers.py`):** `ticket_assigned` (skips self), `ticket_comment_created` (+ mention parsing + contact-reply email).
- **VoIP — 1:** `CallLog.post_save` on terminal status.
- **Comments — 2:** `Comment.post_save/delete` (✅ internal bodies redacted).
- **Contacts — 8:** `Contact/Company/Account/ContactGroup × post_save/delete`.
- **CRM — 4:** `Activity/Reminder × post_save/delete`.
- **Newsfeed — 4:** `NewsPost/NewsPostReaction × post_save/delete`.

## Dual-Write Logging

1. **TicketActivity** — human-readable timeline, **27 `Event`s**. `/api/v1/tickets/tickets/{id}/timeline/`.
2. **ActivityLog** — polymorphic audit trail with diffs+IP, **34 actions**. `/api/v1/tickets/tickets/{id}/activity/`.

**Dedup pattern:** `log_ticket_activity` checks `instance._skip_signal_logging` (set in service sites + `perform_update`); a 2-sec window in `_activity_already_logged` is the safety net.

**Service layer** (`apps/tickets/services.py`, **20 public** functions of 27) — every mutation writes BOTH logs atomically + broadcasts via `on_commit`: `broadcast_ticket_event`, `initialize_sla`, `log_sla_change`, `create_ticket_activity`, `assign_ticket`, `validate_status_transition`, `transition_ticket_status`, `resume_from_wait`, `change_ticket_status`, `close_ticket`, `escalate_ticket`, `change_ticket_priority`, `log_ticket_comment`, `bulk_update_tickets`, `record_first_response`, `merge_tickets`, `split_ticket`, `render_macro`, `apply_macro`, `transition_pipeline_stage`. **⚠ NO `create_ticket()` service fn** — creation is serializer-driven in `TicketViewSet.perform_create`. **`ALLOWED_TRANSITIONS`** (slug-keyed); custom statuses transition freely.

**Kanban drags route through services** for cross-status drags (non-personal boards) — `apps/kanban/services.py::move_card` calls `tickets.services.change_ticket_status(...)`.

**Webhook service** (`apps/tickets/webhook_service.py`): `deliver_webhook` HMAC SHA-256, 10s timeout, auto-disable at 10 failures. 8 EventType members.

## SLA + Business Hours (`apps/tickets/sla.py`)

Single breach-detection entry `get_effective_elapsed_minutes()`: resolves per-tenant schedule via `BusinessHours` (JSON per-day + IANA tz) or legacy `TenantSettings` flat fields; skips `PublicHoliday`; subtracts pause duration. Falls back to **24/7 wall-clock** when no `BusinessHours`. `initialize_sla(ticket)` seeds deadlines.

> **Inbox Hub SLA is separate** — `_initialize_hub_sla` seeds **wall-clock** deadlines; `check_hub_sla_breaches` (Beat 120s) flags + warns + auto-escalates. No business-hours math.

## Inbound / Outbound Email

### Inbound (`apps/inbound_email/`)
- **In-process SMTP server** via `aiosmtpd` (`run_smtp_server`, PM2 `kanzan-smtp`, default `0.0.0.0:2525`). `handle_RCPT` rejects **550** if no tenant resolves; 25MB cap (**552**); optional STARTTLS + LOGIN/PLAIN auth.
- **IMAP poller** — shared mailbox; UID > watermark. Driven by `fetch_inbound_emails_task` (Beat 60s, `kanzan_email`). Disabled when `IMAP_HOST` blank. **Never backfills.** **✅ Dedup PER-TENANT** (`filter(tenant, message_id)`, `imap_poller.py:362` — tenant resolved FIRST).
- **Tenant resolution** — 4 strategies in `resolve_tenant_from_address`: plus-addressing → slug-as-local-part → `TenantSettings.inbound_email_address` → `IMAP_DEFAULT_TENANT_SLUG`.
- **Filters run BEFORE tenant resolution.** Bounces write `BounceLog` + flip `Contact.email_bouncing`.
- **Threading** — `find_existing_ticket` 3-tier: In-Reply-To → References → subject `[#N]`.
- **Processing pipeline** (`process_inbound_email_task`, max_retries=3, acks_late): `select_for_update` → filters → tenant resolution → idempotency claim → find/create contact → find existing ticket OR (per the seam) `park_email_in_hub` else `_create_ticket_from_email` → attach files → **confirmation email queued via `on_commit` ONLY if `auto_send_ticket_created_email` is True** (Feature C — was send-by-default).
- **Agent inbox workflow** (`inbox_services.py`): `link_email_to_ticket`, `action_email`, `ignore_email`.

### Outbound (`apps/tickets/email_service.py`)
- `send_ticket_email()` — single entry point. Skips undeliverable recipients (`.local`, RFC 2606); RFC Message-IDs; reply-to = tenant inbound address or `support+{slug}@{BASE_DOMAIN}`. Persists an OUTBOUND `InboundEmail` (`out:` key). Dev: `filebased.EmailBackend` → `tmp/emails/`. `_add_reply_to_ticket` does NOT fire `ticket_comment_created`; reopens resolved/paused tickets on customer reply.

## Auto-Assign (Inbound Email → Agent — legacy, distinct from Inbox Hub)

`apps/agents/services.py::pick_email_agent(tenant)`:
1. Active member with **`role__hierarchy_level == 30`** (Agent/IT/HR — ⚠ **raw role**, `services.py:226`).
2. Not OFFLINE; agents with no `AgentAvailability` eligible (fail-open). ⚠ Checks only OFFLINE, not `is_assignable`.
3. Fewest open tickets. 4. Tie-break least-recently-assigned (NULLS FIRST).

`auto_assign_email_ticket(ticket)` — gated by `TenantSettings.auto_assign_inbound_email_tickets` (default False).

## VoIP

Asterisk/FreePBX → ARI (REST + WebSocket Stasis). Django wraps ARI, exposes SIP creds to browser softphone (SIP.js over WSS), persists `CallLog`/`CallRecording`.
- **`services.py`** — `check_call_limit` (the **only** `Plan.has_voip` enforcement — now permits Pro/Enterprise), `process_ari_event` → `_broadcast_call_event` → `voip_{tenant_id}`.
- **`consumers.py::CallEventConsumer`** (`ws/voip/events/`). ⚠ **No per-user scoping** — every tenant member joins `voip_{tenant_id}` and sees all call metadata. Bare `close()`.
- **`run_ari_listener`** — one listener per active tenant. **NOT in any PM2 config** — manual launch.
- **Softphone** — SIP.js 0.21.2 (CDN, conditional on `voip_enabled` = `VoIPSettings.is_active`, decoupled from `Plan.has_voip`).

## API Architecture

### Authentication
- **API:** JWT (SimpleJWT) — 15min access, 7-day refresh, rotate+blacklist, HS256. **`APIKeyAuthentication`** (`Authorization: Api-Key kz_live_<slug6>_<secret>`). SHA-512, timing-safe `compare_digest`. Returns `None` (fail-open) when header absent; fails closed (401) on malformed/invalid/revoked/expired/cross-tenant.
- **`DEFAULT_AUTHENTICATION_CLASSES` order:** JWT → APIKey → Session.
- **Frontend:** Session auth (Redis cached_db, host-only cookie). **SSO:** django-allauth (Google, Microsoft, OIDC), email-only. **Global logout:** `User.auth_version` bump via `SessionVersionMiddleware`.

### `/api/v1/` Endpoint Map (23 router includes / 22 unique URLConfs — `inbound-email/` dual-mounts as `emails/`)

```
/tenants/      TenantViewSet, TenantSettingsViewSet (singleton; per-field Manager allowlist incl. inbox_hub_* toggles)
/accounts/     AuthViewSet (throttle "auth"), User, Role, Profile, Invitation, TenantMembership, UserGroup
/api-keys/     APIKeyViewSet (admin-only; mint/list/reveal-once/regenerate/revoke)
/tickets/      TicketViewSet (31 @action), TicketStatus, Queue, TicketCategory, SLAPolicy, EscalationRule, CannedResponse, Macro, SavedView, BusinessHours, PublicHoliday, TicketTemplate, Webhook, CSATSubmitView (public)
/contacts/     ContactViewSet (+context), Company, Account, ContactGroup
/billing/      PlanViewSet (AllowAny), SubscriptionViewSet (+cancel/reactivate), Invoice, Usage, checkout, webhook (CSRF-exempt, Stripe-signed)
/kanban/       BoardViewSet (+detail), Column, CardPositionViewSet (+move/reorder/add-ticket)
/comments/     CommentViewSet, ActivityLogViewSet (read-only)
/messaging/    ConversationViewSet (+add/remove/leave/search-participants), MessageViewSet (+broadcast author-only)
/notifications/  NotificationViewSet (+mark_read, unread_count, admin cleanup), NotificationPreferenceViewSet
/attachments/  AttachmentViewSet (multipart, true-MIME, cross-tenant validated, object-level authz)
/analytics/    DashboardView (APIView, ⚠ IsAuthenticated only), ReportDefinition, DashboardWidget, ExportJob, CalendarEvent
/agents/       AgentAvailabilityViewSet (grant/revoke_temp_role/assignable_roles excl admin), CustomAgentStatus
/custom-fields/  CustomFieldDefinition, CustomFieldValue (read-only)
/knowledge/    Category, ArticleViewSet (+submit_for_review/approve/reject/record_view/remove_file/preview_file/vote), KBSearchView
/notes/        QuickNoteViewSet
/inbound-email/  InboundEmailViewSet (read + create-ticket action; ?assigned=me/?internal=true/?mine=true; +attachment) + InboxViewSet (link/action/ignore)
/emails/       alias mount of inbound_email.api_urls (namespace="emails_api")
/crm/          ActivityViewSet (+my-tasks), ReminderViewSet (+overdue/stats/complete/cancel/reschedule/bulk-action; ?mine=true&status=pending), PipelineForecastView
/nav/          BadgeCountView (7 categories incl. inbox_hub; effective_role; capped at 99)
/newsfeed/     NewsPostViewSet (+react/mark-read/mark-all-read/unread-count)
/voip/         VoIPSettings, Extension, CallLog (+active/stats), InitiateCall, CallHold, CallTransfer, CallHangup, SIPCredentials, CallRecordingDownload, CallQueue
/inbox-hub/    HubEmailViewSet (list/retrieve + 10 actions) + Department + RoutingRule + HubEmailSLA + QueueRouting
```

**Non-HTTP inbound channel:** `kanzan-smtp` PM2 process (`0.0.0.0:2525`). **Docs:** `/api/docs/` (Swagger), `/api/schema/` (OpenAPI 3.0). **DRF config:** `DEFAULT_PERMISSION_CLASSES=[IsAuthenticated]`; throttles `[ScopedRateThrottle, APIKeyRateThrottle]`; rates `auth 10/min, api_default 200/min, api_heavy 30/min, webhook 60/min, api_key 1000/hour`; PAGE_SIZE 50.

### Public / unauthenticated endpoints
`POST /api/v1/tickets/csat/` (signed token), `GET /api/v1/billing/plans/` (`AllowAny`), `POST /api/v1/billing/webhook/` (HMAC, `@csrf_exempt`), `AuthViewSet.register/login/accept_invitation` (`AllowAny`, throttle `auth`).

### Frontend Routes (`apps/tenants/frontend_urls.py`) — 36 routes

```
/ login /register /logout /auth/handoff/ /verify-email/ /verify-email-sent/
/setup-company/ (login)  /workspaces/ (login)
/dashboard/ /tickets/ /tickets/new/ /tickets/<num>/ /contacts/ /contacts/create/ /contacts/<id>/
/calendar/ /kanban/ /messaging/ /analytics/ /knowledge/ /knowledge/<slug>/ /profile/ /api/quickstart/
/reminders/ /calls/ /inbound-email/ /audit-log/  (all @_membership_required)
/users/ /billing/ /agents/ /groups/  (@_role_required(20))
/inbox/  (@_role_required(30) — agent-level personal inbox; view func emails_page; labelled "Inbox"; was /emails/)
/settings/  (@_membership_required + @ensure_csrf_cookie — API enforces admin write)
/emails/  (@_inbox_hub_access_required — triage cockpit; view func inbox_hub_page; labelled "Emails"; was /inbox-hub/ → 403.html)
/inbox-hub/  (RedirectView 302 → /emails/ — legacy-bookmark bounce, name=inbox-hub-redirect)
```

> ⚠️ **Name↔label mismatch is INTENTIONAL** (uncommitted rename): `/emails/` → view `inbox_hub_page`, name `emails`; `/inbox/` → view `emails_page`, name `inbox`. Template dirs (`pages/inbox_hub/`, `pages/emails/`), the `inbox_hub` app, and the `/api/v1/inbox-hub/` API keep their original names.

## WebSocket Endpoints (6 total — `main/asgi.py`)

Stack: `ProtocolTypeRouter({"http": …, "websocket": AllowedHostsOriginValidator(AuthMiddlewareStack(WebSocketTenantMiddleware(URLRouter(messaging + notification + ticket + voip + live))))})`.

1. **Chat:** `ws/messaging/{conversation_id}/` → `ChatConsumer`. Group `chat_{id}`. 10KB/msg, 5 msg/s, 2s typing. Close 4001/4002/4003/4004. ⚠ `_create_message` hardcodes `"attachments": []`.
2. **Notifications:** `ws/notifications/` → `NotificationConsumer`. Group `notifications_{user_id}`. Bare `close()`. **The rail the reminder-due popup rides.**
3. **Ticket Presence:** `ws/tickets/{ticket_id}/presence/` → `TicketPresenceConsumer`. **Known gap:** `presence_list` for newcomers not implemented.
4. **Ticket Feed:** `ws/tickets/feed/` → `TicketListConsumer`. Group `ticket_feed_{tenant_id}`. Read-only.
5. **VoIP:** `ws/voip/events/` → `CallEventConsumer`. ⚠ Tenant-wide call metadata. Bare `close()`.
6. **Live:** `ws/live/` → `LiveEventConsumer`. Stamps presence on connect + each `ping`.

> `apps/inbox_hub/routing.py` is the **RoutingEngine** (email→department), NOT a Channels route.

## Celery Tasks & Beat Schedule

### Queue Routing (`main/celery.py` — 8 globs incl. default)
```
apps.billing.tasks.*                              → kanzan_webhooks   (DORMANT — apps/billing/tasks.py does not exist)
apps.notifications.tasks.send_email_* / send_notification_email → kanzan_email
apps.inbound_email.tasks.*                        → kanzan_email
apps.tickets.tasks.send_ticket_*                  → kanzan_email
apps.api_keys.tasks.send_api_key_*                → kanzan_email
apps.voip.tasks.*                                 → kanzan_voip       (⚠ no PM2 worker subscribes)
*                                                 → kanzan_default
```
**`CELERY_BEAT_SCHEDULE` lives in `main/settings/base.py`.**

### Beat Schedule (12 tasks)

| Beat key | Task | Schedule |
|----------|------|----------|
| `check-sla-breaches` | `apps.tickets.tasks.check_sla_breaches` | 120s |
| `check-overdue-tickets` | `apps.tickets.tasks.check_overdue_tickets` | 900s |
| `cleanup-old-notifications` | `apps.notifications.tasks.cleanup_old_notifications` | 86400s |
| `calculate-lead-scores` | `apps.crm.tasks.calculate_lead_scores` | 86400s |
| `calculate-account-health-scores` | `apps.crm.tasks.calculate_account_health_scores` | 86400s |
| `kb-stale-alert` | `knowledge_base.alert_stale_articles` | crontab daily 08:00 (UTC) |
| `kb-gap-digest` | `knowledge_base.send_gap_digest` | crontab Mon 09:00 (UTC) |
| `cleanup-stale-calls` | `apps.voip.tasks.cleanup_stale_calls` | 3600s (⚠ piles up — kanzan_voip unconsumed) |
| `fetch-inbound-emails` | `apps.inbound_email.tasks.fetch_inbound_emails_task` | 60s |
| `reap-stale-presence` | `apps.agents.tasks.reap_stale_presence` | 60s |
| `check-hub-sla-breaches` | `apps.inbox_hub.tasks.check_hub_sla_breaches` | 120s |
| `fire-due-reminders` | `apps.crm.tasks.fire_due_reminders` | 30s |

Celery Beat uses the **built-in shelve scheduler**. **`apps.crm.tasks.check_overdue_reminders` and `apps.tickets.tasks.check_sla_breach_warnings` exist but are NOT in Beat** (dead). 27 tasks total; 15 unscheduled.

## PM2 Processes — 5 prod / 4 dev

### `ecosystem.config.js` (prod, venv `.venv/`)

| Name | Purpose |
|------|---------|
| `kanzan-django` | `gunicorn main.asgi:application -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8001 --timeout 120` (2GB) |
| `kanzan-celery-worker` | `celery -A main worker -Q kanzan_default,kanzan_email,kanzan_webhooks -c 4 --max-tasks-per-child=200` (2GB) |
| `kanzan-celery-beat` | `celery -A main beat -l info` |
| `kanzan-flower` | `celery -A main flower --port=5556 --basic_auth=$KANZAN_FLOWER_AUTH` |
| `kanzan-smtp` | `manage.py run_smtp_server` (2525) |

> **⚠ Worker `-Q` = `kanzan_default,kanzan_email,kanzan_webhooks`** — `kanzan_voip` unconsumed (3 voip tasks + Beat `cleanup-stale-calls` accumulate). `run_ari_listener` **not in PM2**. **Makefile `stop`/`restart` omit `kanzan-smtp`.**

### `ecosystem.dev.config.js` (dev, venv `env/` → `.venv/`) — 4 processes
- `kanzan-django` `runserver`. `kanzan-celery-worker` `-c 2`, **watch** on `apps/*/{tasks,services}.py` + `main/celery.py`. `kanzan-celery-beat`, `kanzan-flower`. **No `kanzan-smtp`.**

## Frontend Architecture

### JavaScript (`static/js/`, 14 modules, 5,919 LOC — vanilla, no React/Vue)

| Module | LOC | Role |
|--------|----:|------|
| `inbox-hub.js` | **1,441** | Triage COCKPIT (`?v=13`, labelled "Emails"): 5 lenses, 4-count fetch, customer-context card, **assignee chip**, attachment thumbnails, SLA badge, full `#ihConvertPanel` offcanvas (TipTap + tags + flatpickr) / assign / dismiss, J/K/C/A/X/Esc, 7 LiveBus subs (400ms). NO claim/escalate/transition/note UI |
| `app.js` | **1,273** | Global init: alerts, sidebar, notification WS, Toast (now `window.Toast`-published), `Kanzan.formatDate/…`, badges, live-status pill, **`ReminderAlerts` IIFE** (Feature A) + **`ReminderScheduler` IIFE** (Feature C, uncommitted) |
| `voip-softphone.js` | 710 | SIP.js 0.21.2 + `CallEventConsumer` |
| `custom-select.js` | 371 | `KanzenSelect` portal-rendered styled selects |
| `command-palette.js` | 337 | Cmd+K modal (⚠ "New Contact" → dead `/contacts/new/`) |
| `keyboard-shortcuts.js` | 318 | Global hotkeys; injects runtime `<style>` |
| `ticket-feed.js` | 248 | `ws/tickets/feed/` → republishes into LiveBus |
| `agent-availability.js` | 244 | Status toggle + `subscribePresence()` |
| `notes-panel.js` | 238 | Quick notes CRUD |
| `live-connection.js` | 206 | Single shared `ws/live/`, 25s heartbeat / 8s pong, backoff 1s→30s (infinite) |
| `rich-editor.js` | 191 | TipTap wrapper |
| `live-bus.js` | 175 | Global pub/sub `window.LiveBus` (BroadcastChannel cross-tab) |
| `api.js` | 90 | Central API client (CSRF cookie + meta fallback) |
| `theme.js` | 77 | light/dark/system (default dark). Loaded SYNCHRONOUSLY in `<head>` |

> ⚠ Multiple independent notification-WS backoffs: `app.js initNotifications` (max **10**) vs `live-connection.js` (**infinite**) vs `ticket-feed.js` (10) vs `voip-softphone.js` (5s fixed). Only `inbox-hub.js` is cache-busted (`?v=13`).

### CSS & Theming

- **`static/css/custom-v15.css`** — **25,225 LOC on disk** (HEAD 25,149 + uncommitted `.activity-badge`/`.ih-assignee-chip` blocks), "Design System v9.0 Crimson Black" (the only loaded project CSS). Token scales: `--crm-radius-{xs:4,sm:8,md:6,lg:14,xl:16,pill:9999}` (⚠ `md`<`sm` quirk), `--crm-weight-{normal:400,medium:500,semibold:600,bold:700}`, `--crm-z-{base:1,sticky:10,dropdown:1000,modal-backdrop:1040,modal:1050,popover:1060,tooltip:1070,flyout:1080,overlay:1085,toast:1090}`, plus `--crm-text-*` size scale + duration/easing tokens. Includes Inbox Hub cockpit + `.ih-convert-*` offcanvas + `.reminder-due-*` blocks (token-only).
- **`static/css/custom.css`** — 20,431 LOC committed snapshot, NOT loaded, allowlisted.
- **No hex literals in rule bodies.** `make theme-check` enforces against `scripts/.theme_baseline.json` (**baseline 147 hex / 11 files** — custom-v15.css 81, landing_crm.html 21, landing.html 15, dashboard.html 13, contacts/list 5, kanban/board 4, verify_email_sent 3, tickets/detail 2, login/tickets-list/reminders-list 1 each). **PASSES** (runtime "146 tracked"). ⚠ `base.html` toast container has a hardcoded inline `z-index:1090` (invisible to the hex-only check).

### Templates (48 .html files)

- `templates/base.html` (292 lines) — palette `<style>`, toast container, quick-notes panel, **`#reminderDueModal`** (static backdrop), softphone (conditional), DOMPurify 3.2.4 + SIP.js 0.21.2 CDN + Flatpickr loader + synchronous `kanzan_sidebar_collapsed` pre-paint. Default theme: dark.
- `templates/includes/` (6 files): `navbar.html`, `sidebar.html` (Inbox section gated `{% if can_access_inbox_hub %}`; **Emails**=`ti-mail`→`/emails/`, **Inbox**=`ti-inbox`→`/inbox/`), `softphone.html` (conditional), `messages.html`, `page_back_button.html` (included by **18**), `kb_sidebar_widget.html` (**ORPHAN**).
- `templates/pages/` — **18 subfolders + 8 root files** (403, api_quickstart, calendar, dashboard, landing, login, profile, register).
- Notable: `reminders/list.html` (split-pane, NL quick-add, `reminder.due` LiveBus, flatpickr scroll-gap fix); `emails/list.html` (now titled **"Inbox"**; `#createTicketModal`, dual-source load); `inbox_hub/list.html` (now titled **"Emails"**; `#ihConvertPanel`, assignee chip, `inbox-hub.js ?v=13`); `settings/tenant.html` (2 Inbox Hub toggles + the auto-send toggle [Feature C: now unchecked by default]); `dashboard.html` (restructured recent-activity rows: `.activity-header` + `.activity-badge`); `audit_log/list.html`; `tickets/list.html` (dynamic per-status tabs); `tickets/detail.html` (Delete-Ticket removed; macro JS dead no-ops).

### Context Processor (`apps/tenants/context_processors.py`)

Injects: `tenant`, `membership` (cached on `request._cached_tenant_membership`), `user_role` (= `effective_role`), `is_admin`/`is_admin_or_manager`/`is_agent_or_above`, **`can_access_inbox_hub`**, `voip_enabled` (= `VoIPSettings.is_active`), `tenant_palette` (~21-key), `BASE_URL`.

## Middleware Stack (14 layers)

1. SecurityMiddleware 2. WhiteNoiseMiddleware 3. SessionMiddleware 4. CorsMiddleware 5. CommonMiddleware 6. CsrfViewMiddleware 7. AuthenticationMiddleware 8. AccountMiddleware (allauth) 9. **SessionVersionMiddleware** (global logout via `auth_version`) 10. **TenantMiddleware** (`/admin/` dedicated branch; after auth) 11. **SubscriptionMiddleware** (402 when neither active nor in grace) 12. **RateLimitHeadersMiddleware** 13. MessageMiddleware 14. XFrameOptionsMiddleware

## Billing Plans (⚠ FROM THE SEEDER — `seed_plans.py`)

| Plan | $/mo | Users | Contacts | Tickets/mo | Storage | VoIP | Recording | Calls/mo |
|------|---|-------|----------|-----------|---------|------|-----------|----------|
| Free | 0 | 3 | 500 | 100 | 1GB | No | No | 0 |
| Pro | 29 | 25 | 10K | 5K | 25GB | Yes | Yes | 1000 |
| Enterprise | 99 | ∞ | ∞ | ∞ | ∞ | Yes | Yes | ∞ (None) |

> **✅ VoIP entitlement seeded (Sprint 0); mig `billing/0003` backfills existing rows.** `check_call_limit` permits Pro/Enterprise. Softphone *UI* visibility still gated by `VoIPSettings.is_active` (decoupled from `Plan.has_voip`). `require_feature("voip")` decorator still 100% dead.

## Management Commands (8 total)

```bash
python manage.py provision_tenant --name "Acme" --slug acme [--domain crm.acme.com]
python manage.py seed_plans                                    # Free/Pro/Enterprise (sets VoIP flags)
python manage.py setup_queues --tenant-slug demo
python manage.py setup_ticket_statuses --tenant-slug demo
python manage.py backfill_sla_audit [--tenant-slug] [--dry-run]
python manage.py seed_inbox_hub_defaults [--tenant-slug <slug> | --all-tenants]  # General dept only
python manage.py run_smtp_server                               # kanzan-smtp PM2 process
python manage.py run_ari_listener                              # VoIP Stasis event loop (NOT in PM2)
```

## Environment Variables

- **`.env.example` (16 keys):** `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DATABASE_URL`, `REDIS_URL`, `BASE_DOMAIN`, `BASE_SCHEME`, `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`, `JWT_SECRET_KEY`, `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `KANZAN_FLOWER_AUTH`.
- **base.py reads ~37 env keys; ~21 are read-but-undocumented:** `BASE_PORT`, `DEFAULT_FROM_EMAIL`, `EMAIL_TIMEOUT`, `EMAIL_USE_SSL`, `USE_X_ACCEL_REDIRECT`, `X_ACCEL_MEDIA_PREFIX`, `INBOUND_EMAIL_WEBHOOK_SECRET`, 7×`IMAP_*`, 7×`SMTP_SERVER_*`, `AGENT_PRESENCE_TTL_SECONDS`, `AGENT_PRESENCE_AUTO_ONLINE`, `HUB_SLA_WARNING_MINUTES`.
- **`KANZAN_FLOWER_AUTH` is documented but NOT read by `base.py`** — consumed only by `ecosystem.config.js`.

## Testing

- **Framework:** pytest + pytest-django. **78 modules** (68 root + 10 app-level). `pytest.ini`: 3 lines (`DJANGO_SETTINGS_MODULE=main.settings`, `pythonpath=.`, **no `asyncio_mode`** → defaults `strict`; `dev.txt` ships `pytest-asyncio`).
- **Fixtures (`conftest.py`, 343 LOC):** **16 factories + 20 fixtures** (3 autouse: `celery_eager`, `free_plan`, `clear_tenant_context`). `ReminderFactory` sets `priority="medium"` + `scheduled_at=now()`.
- **✅ FULL SUITE GREEN — verified 2026-06-23:** `python -m pytest -q` = **1048 passed / 0 failed / 22 skipped / 1 xfailed** (~212s on SQLite; 1071 collected). The 22 skips are env-gated (Postgres-only FTS etc.); the 1 xfail expected. `makemigrations --check` clean; `make theme-check` green; `ruff check .` = 197 lint issues (non-blocking).
- ⚠ `make test-fast` uses `--timeout=30` but `pytest-timeout` is STILL NOT in `dev.txt`.

## Documentation

- `/CLAUDE.md` (this file) — day-to-day source of truth.
- `/docs/README.md` — index; defers to `/CLAUDE.md`.
- `/docs/architecture.md` — long-form (Version 1.0, **2026-02-06**; STALE).
- `/docs/ui-consistency-audit.md` (2026-05-22) — figures outdated.
- `/docs/deploy/protected-media.md` — prod media authentication strategy (X-Accel-Redirect).
- `/docs/reference/{codebase-inventory,api-surface,frontend-surface,infra-surface}.md` — **STALE** (predate inbox_hub, the access refactor, presence layer, the features, Sprint 0). CLAUDE.md wins on any disagreement.
- **`/docs/qa-audit-2026-06-14/` (11 files)** — the end-to-end QA/security/perf audit that drove the sprint-0 branch. `00-EXECUTIVE-SUMMARY` (38/100 pre-fix), `01`–`06` reports + 4 `_digest_*` appendices. Still-current for the Sprint 1–3 backlog.
- `/scripts/check_theme.py` + `.theme_baseline.json` — regression guard (baseline 147 / 11 files).
- `README.md` — minimal stub.

## Common Pitfalls & Fixes Applied

1. `ACCOUNT_LOGIN_METHODS = {"email"}` (a set); all apps need `migrations/__init__.py`; DRF ≥3.16 (Django 6); `django-celery-beat` removed; `django.contrib.postgres` installed (KB FTS).
2. **`daphne` + `jazzmin` ARE in INSTALLED_APPS** (admin is jazzmin "darkly", superuser-locked; `main/admin.py` registers 0 models).
3. **`main` @ `94468bc` == `origin/main`** for the last *commit* — but the **working tree is DIRTY** with Feature C + the Emails/Inbox rename + two behaviour reversals (creator keeps tickets after handoff; TICKET_ASSIGNED no longer emailed). Counts: migrations **120** (1 untracked = `tenants/0011`), models **91**, test modules **78**, beat 12, tasks 27, NotificationType 21, INTERNAL_ONLY_TYPES **6**, ActivityLog 34, TicketActivity 27, custom-v15.css **25,225**, app.js **1,273**/JS **5,919**, pytest **1048 pass**.
4. **Viewer IS seeded — 7 system roles**. **`apps.nav` is NOT installed** (21 `apps.*`).
5. **91 model classes** (Django registry authoritative), 5 polymorphic GenericFK. **Notification is NOT polymorphic.**
6. **`/admin/` NOT in `EXEMPT_PATH_PREFIXES` (17 entries)**. `IsTenantMember` returns True when no tenant. **`/inbound/email/` exempt entry is dead.**
7. **DRF auth order JWT → APIKey → Session.** API keys SHA-512, shown once.
8. **Always use `effective_role`** — BUT raw-`role` drift remains at 4 sites: `agents/services.py:226`, `inbox_hub/assignment.py:228`, `tickets/views.py:1052`/`:1115`.
9. **⚠ Inbox Hub access is DEPARTMENT-SCOPED** (`inbox_hub/access.py`; UserGroup gate DELETED) — Admin/Manager (≤20) always; Viewer (>30) never; agent-tier iff (in active dept) OR (no active depts, fall-open) OR (has assigned mail). Dept-having tenants must make triagers Department members or they 403.
10. **⚠ Agent ticket visibility — creator NOW KEEPS the ticket after handoff** (`tickets/access.py`, UNCOMMITTED): `Q(assignee=me) | Q(created_by=me)`. **This REVERSES the prior "self-created handed-off ticket leaves the creator's view" rule.**
11. **Agent email-inbox handoff** — manual Hub assign/reassign/claim stamps `InboundEmail.assignee`; **auto-assign does NOT**.
12. **Inbox Hub frontend is a TRIAGE COCKPIT** (`?v=13`, labelled "Emails", with an assignee chip). Backend `claim/escalate/transition/note` exist but UI never calls them. **No `reply` action.**
13. **Inbox Hub presence is heartbeat-driven** — `/ws/live/` 25s ping stamps `last_seen`; `reap_stale_presence` (60s) ages ONLINE→AWAY at 90s TTL; `is_assignable` is the single auto-assign gate.
14. **`first_responded_at` stamped by `transition_hub_email`** — but the cockpit never calls `transition`, so in practice the response-breach still usually fires (+auto-escalates). **`escalate_hub_email` bumps `escalation_count` even on illegal transition.** **`PARKED_IN_HUB` write-dead.**
15. **`apps/inbox_hub/routing.py` is the RoutingEngine**, NOT a Channels route. `apps/inbox_hub/` has NO `signals.py`/`ready()`.
16. **Feature B reachability gap CLOSED** — both surfaces share `ticket_overrides.py::build_ticket_overrides`; full 9-field set reaches both. **Inbound-email attachments** — `attachments.py` + authed `attachment/` action on both viewsets (inline raster / forced download + `nosniff`).
17. **`process_inbound_email` variable-shadowing** — local `settings` rebinds module-level `django.conf.settings`. **IMAP "never backfill".** Filters run BEFORE tenant resolution. **Feature C: confirmation email now sent ONLY if `auto_send_ticket_created_email` is explicitly True (default False).**
18. **BILLING VoIP flags SEEDED** (mig `billing/0003`); re-subscribe repoint. **`require_feature` still 100% dead.** `voip_enabled` gated by `VoIPSettings.is_active`.
19. **VoIP runtime is manual** — `kanzan_voip` queue unconsumed; `run_ari_listener` not in PM2; `cleanup-stale-calls` Beat messages pile up. `CallEventConsumer` tenant-wide (no per-user scoping).
20. **`check_overdue_reminders` + `check_sla_breach_warnings` dead** (not in Beat). **`fire_due_reminders` IS live @30s** (Feature C adds the client-side `ReminderScheduler` for exact-time popups).
21. **Company custom fields synced** (`custom_fields/signals.py` `Company.post_save`). `Account.health_score`/`Contact.lead_score` clamp only in uncalled `clean()`.
22. **Internal comments redacted on the live channel** (`comments/signals.py` → `body: None if is_internal`). **ContactEvent emits ZERO live events.** `TicketPresenceConsumer presence_list` unimplemented.
23. **Kanban drags → ticket service** for cross-status drags (non-personal boards). Pipeline-stage→column sync matches by **name** (rename breaks it).
24. **`Ticket.save()` auto-fills `company`** from linked Contact. **`Article.save()` resolves tenant from context.** `TicketActivity` inner enum is `Event`, NOT `EventType`.
25. **ActivityLog 34 / NotificationType 21 (+REMINDER_DUE) / INTERNAL_ONLY_TYPES 6 (+TICKET_ASSIGNED) / TicketActivity 27.** **HubEmail 9 states / 4 priorities** (no medium; Reminder HAS medium).
26. **`Queue.department` FK opt-in.** **`Board.is_personal`** private. **`Conversation.source_group`** dedupes group convs. **`UserGroup`** "one user per group" (no longer the Hub gate).
27. **No hex literals in CSS/JS/template rule bodies** — `make theme-check` (baseline 147/11). ⚠ `base.html` toast `z-index:1090` hardcoded.
28. **`tickets/detail.html` Delete-Ticket REMOVED**; macro JS dead no-ops. **`tickets/list.html` stat tabs dynamic.** **Reminders v2 NL quick-add.**
29. **`kb_sidebar_widget.html` orphan** (safe-delete). **`page_back_button.html` included by 18.** **`KBRevision`/`KBTicketLink` dead-write.** **`command-palette.js` "New Contact" → dead `/contacts/new/`.**
30. **CI EXISTS** (`.github/workflows/ci.yml`, PG16+Redis7). **`requirements.txt` byte-identical to `requirements/base.txt`.** **Logs not rotated.** **`make logs-django` errors.** **`make stop`/`restart` skip `kanzan-smtp`.** **`pytest-timeout` missing from `dev.txt`.** **`analytics.DashboardView` under-permissioned.**
31. **Security gaps CLOSED:** DEBUG default→False + `main/checks.py::kanzen.E001` enforcement; messaging tenant-scoping; attachment object-level authz + **authed `/media/`** (`media_views.py`); IMAP per-`(tenant,message_id)` dedup; Stripe replay guard; message-edit fix; HSTS-preload. **`makemigrations --check` clean; 120 migrations** (1 untracked: `tenants/0011`).
32. **`window.Toast` now published** (`app.js` end) — previously `const Toast` was module-scoped only, so `window.Toast` feature-detection in other scripts silently fell back to `console.log`/`alert`.
