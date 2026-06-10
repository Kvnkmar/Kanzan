# Kanzen — Project Intelligence

> Last refreshed: **2026-06-10** — verified against branch `main` @ HEAD `8682e80` ("new updates") via a fresh from-scratch deep-dive (7 parallel agents; CLAUDE.md + MEMORY.md detached and re-derived from the code, then key files re-read directly). HEAD is unchanged, but **the uncommitted working tree has grown again past the "triage-only desk" the prior doc described** — it is now **39 modified + 14 untracked files, +4,246 / −1,444 lines vs HEAD** (CLAUDE.md itself is one of the modified files). The prior CLAUDE.md was written 2026-06-09 02:28 — **hours before a Jun-9 03:33–04:51 access-control refactor that it does not capture.** `makemigrations --check` is **clean**; **3 untracked migrations** (`agents/0007_agentavailability_last_seen`, `tenants/0010_tenantsettings_inbox_hub_auto_assign_and_more`, `inbound_email/0010_inboundemail_assignee_and_more`).
>
> **THREE big working-tree shifts the prior doc got WRONG / missed:**
> 1. **NEW Jun-9 access-control refactor — 3 untracked modules consolidating + tightening row-scoping.** (a) **`apps/tickets/access.py`** (36 LOC) is now the single source of truth for agent ticket visibility: `agent_visible_tickets_q(user)` = `Q(assignee=user) | (Q(created_by=user) & Q(assignee__isnull=True))` + object-level `agent_can_see_ticket`. **BEHAVIORAL CHANGE:** an agent who *created* a ticket now LOSES visibility once it's handed off to another agent (old rule was the looser `created_by OR assignee` — creator kept it forever). Wired into 5 surfaces: `tickets/views.py` (list), `accounts/permissions.py::IsTicketAccessible` (object), `analytics/services.py` (dashboards), `nav/views.py` (badge), `kanban/serializers.py` (cards). (b) **`apps/inbox_hub/access.py`** (52 LOC) **REPLACES the Hub's department-based row-scoping with a GROUP-MEMBERSHIP access gate**: `can_access_inbox_hub(membership)` = Admin (`level ≤ 10`) OR member of ≥1 `UserGroup`. Everyone Manager-and-below in **no** group is fully locked out (hidden nav, zeroed badge, 403 on page + API). Wired into permissions, viewset queryset, `frontend_views` (new `_inbox_hub_access_required` decorator → 403.html), `context_processors` (`can_access_inbox_hub` flag → sidebar `{% if %}`), and the badge. The old Team-Lead-sees-their-department visibility is **gone** — TLs are now agent-tier (NEW + assigned-to-me). (c) **`apps/contacts/context.py`** (119 LOC) extracts `build_contact_context()` out of `ContactViewSet`, **enriches** it (adds company/account/last_activity), bumps the cache prefix to `contact_context_v2`, and is now shared with the new Hub cockpit.
> 2. **The Inbox Hub frontend grew from "triage desk" into a TRIAGE COCKPIT.** `inbox-hub.js` is now **1,177 LOC** (was 828), cache-bust **`?v=8`** (was `?v=6`). The left rail is **5 workload LENSES** (`all` / `unassigned` / `mine` / `oldest` / `sla`), NOT 3 priority filters — the **SLA lens self-hides** when its count is 0 (no SLA policies seeded by default). Counts are 4 parallel HEAD requests (all/unassigned/mine/sla). Biggest addition: a **customer-context card** that calls a NEW **`GET /api/v1/inbox-hub/hub-emails/{id}/context/`** action (`HubEmailViewSet.context`, codename `hub_email.view`) — served off the Hub viewset, NOT `/contacts/{id}/context/`, so it respects Hub row-scoping (agents can't reach a freshly-parked contact via the contacts endpoint). Plus an SLA badge (info/warning/danger by ≤60min/≤15min/overdue) and an "N open tickets" duplicate-ticket nudge. Triage actions unchanged: convert/assign/dismiss + floating body-portaled Assign menu; keybinds J/K/C/A/X/Esc. The HubEmail **list serializer is now a "cockpit" projection** — adds `contact_id`, an HTML-stripped 140-char `snippet`, `has_attachments`/`attachment_count`, and promotes `sla_response_due_at`/`response_breached`/`first_responded_at` to the list row. The `claim`/`escalate`/`transition`/`note` backend endpoints still exist but the UI never calls them.
> 3. **`BadgeCountView` badge semantics were rewritten** (`apps/nav/views.py`): `_email_count` is now a **personal-inbox** count (`assignee=me` ∪ internal-to-me, `inbox_status IN (pending, linked)`, excl. BOUNCED — mirrors `/emails/`), not tenant-wide `is_read=False`; `_message_count` now counts **unread CHAT messages** (`ConversationParticipant`/`Message` vs `last_read_at`), NOT unread comments — **this breaks 2 stale tests** in `test_badges.py` (14.05, 14.09 assert the old comment semantics); `_inbox_hub_count` counts only `state=NEW` tenant-wide and returns 0 for group-locked-out members; `_ticket_count` adds `is_deleted=False` + uses `agent_visible_tickets_q`; `is_agent` uses `effective_role`.
>
> Also this refresh: **`tickets/detail.html` REMOVED the Delete-Ticket feature** (button + modal + handlers) and the **macro dropdown** from the comment composer (Flatpickr time-input fix + email modals re-homed to `<body>` + company auto-fill reflection retained). **`tickets/list.html`** replaced hardcoded Open/In-Progress tabs with **dynamic per-status stat tabs** (`buildStatusTabs` from `/tickets/ticket-statuses/`, event delegation). **`reminders/list.html`** (now 3,635 LOC) gained a **natural-language quick-add parser** (`parseQuickAddTime`: "call Acme tomorrow 4pm urgent" → date+priority) + a 5-col stat grid + body-portaled Filters popover. **`audit_log/list.html`** export now mirrors live filters + walks all paginated pages (`fetchAllLogs`, was silently truncated to 50). `analytics/services.py` adds `is_deleted=False` to the dashboard querysets. **Verified factual counts** (this refresh):
> - **91 Django model classes** across 21 apps with `models.py` (per-app: tickets 22, inbox_hub 8, accounts 8, knowledge 6, contacts 5, voip 5, analytics 4, billing 4, comments 4, kanban 3, inbound_email 3, messaging 3, newsfeed 3, agents 2, crm 2, custom_fields 2, notifications 2, tenants 2, api_keys 1, attachments 1, notes 1). Raw `^class` grep gives **102** — the extra 11 are nested `TextChoices`/`IntegerChoices`/`Manager`/`QuerySet` subclasses, not Django models.
> - **116 migrations total** across 21 apps; **3 untracked** (`agents/0007`, `tenants/0010`, `inbound_email/0010`). `inbox_hub` still has exactly **1 migration** (`0001_initial`, all 8 models + 5 indexes + conditional uniques).
> - **23 `/api/v1/*/` `path(...)` includes** in `main/urls.py` (22 unique URLConfs — `inbound-email/` dual-mounts as `emails/`).
> - **35 frontend URL paths** in `apps/tenants/frontend_urls.py` (`/inbox-hub/` now gated by `_inbox_hub_access_required`, not bare `_membership_required`).
> - **6 WebSocket consumers** at `ws/messaging/<id>/`, `ws/notifications/`, `ws/tickets/<id>/presence/`, `ws/tickets/feed/`, `ws/voip/events/`, `ws/live/`. (Note: `apps/inbox_hub/routing.py` is the **RoutingEngine**, NOT a Channels route.)
> - **26 Celery `@shared_task` functions** across **10 task modules** (incl. `apps/agents/tasks.py`, `apps/inbox_hub/tasks.py`); **11 in Beat schedule** (incl. `reap-stale-presence` 60s, `check-hub-sla-breaches` 120s).
> - **63 test modules total** (56 root + 7 app-level). **Verified run 2026-06-10:** Inbox Hub `test_inbox_hub.py` (**21**, was 13) + `test_inbox_hub_routing_assignment.py` (**38**, was 25/27) = **59 passed**; `test_inbound_email.py` (**14**) passed; `test_access_control.py` (**12**, +1 new — the ticket-handoff-visibility test) passed; **`test_badges.py` 36 passed / 2 FAILED** (14.05 + 14.09 — stale tests asserting the old unread-comments "messages" badge, now repurposed to unread-chat; intentional behavior change, tests not updated). `apps/tickets/tests/test_creation.py` 17 passed / 1 skipped (SQLite SELECT-FOR-UPDATE).
> - **`static/css/custom-v15.css` is now 24,934 LOC** (HEAD 24,622; +312 net — Inbox Hub section rewritten for the cockpit: added `.ih-context-*` card block, `.ih-sla-badge--*`, lens/avatar rules; theme-check still **PASSES**, 146 hex tracked vs baseline 147, zero new leakage).
> - **48 `.html` templates**; **18 `templates/pages/` subfolders**.
> - **14 JS files / 5,262 LOC** in `static/js/` (`inbox-hub.js` **1,177**; `agent-availability.js` 244 with `subscribePresence()`).
> - **42 signal receivers** across 10 apps with `signals.py` + `notifications/signal_handlers.py`. **`apps/inbox_hub/` still has NO `signals.py`** — its `apps.py` has no `ready()`; it fans events from its service/routing/assignment layer, not signals.
> - **8 management commands** (incl. `seed_inbox_hub_defaults`).
> - **`logs/` is now ~74MB** (gitignored runtime artifact) — `celery-worker-error.log` ~40MB, `celery-beat-error.log` ~15MB, `django.log` ~14MB, `django-error.log` ~5.7MB. Still no rotation.
> - **33 Makefile targets** (`logs-django` declared in `.PHONY` but has no rule body — calling it errors).
> - **Repo is tidy**: no stray Claude temp files, no committed junk, no root screenshots/`.bak`/scratch files. `static/images/DP.png` is a legitimate committed favicon. `env` is a committed symlink → `.venv`. Only gitignored runtime artifacts (`logs/`, `tmp/emails/`, caches, `db.sqlite3`, `celerybeat-schedule`, `media/` uploads) exist on disk.

## Project Overview

Multi-tenant CRM, Ticketing, Knowledge Base and VoIP SaaS. **Django 6.0.2 + DRF 3.16+ + Channels 4.2+ + Celery 5.4+** with Bootstrap 5.3.3 + vanilla JS frontend (SIP.js softphone, TipTap rich editor, DOMPurify sanitization). Row-level multi-tenancy via subdomain routing and **contextvars-based** tenant binding (async-safe). PM2 process management.

**Port:** 8001 (ASGI via Gunicorn + Uvicorn worker) | **Dev DB:** SQLite | **Prod DB:** PostgreSQL
**Redis:** db3 (cache + cached_db sessions, prefix `kanzan`), db4 (Celery broker + django-db result backend), db5 (Channels layer, prefix `kanzan:channels`)
**SMTP in-process server:** 2525 (kanzan-smtp PM2 process) | **Flower:** 5556 | **TIME_ZONE:** `Asia/Kuala_Lumpur` (Celery UTC; `USE_TZ=True`)

## Quick Reference

```
Superuser:      admin@kanzen.local / Pl@nC-ICT_2024
Django Admin:   http://localhost:8001/admin/   (locked to is_superuser — see main/admin.py)
Tenants:
  Straat-X:     http://straat-x.localhost:8001
Flower:         http://localhost:5556 (admin:changeme — KANZAN_FLOWER_AUTH)
API Docs:       http://straat-x.localhost:8001/api/docs/
```

## Project Structure

```
/home/kavin/Kanzen/
├── apps/                          # 22 Django apps in INSTALLED_APPS (21 with models.py; nav is URL-only)
│   ├── accounts/                  # Users (+is_service_account), 7-role RBAC + temp-role overrides + temp-perms intersection, invitations, profiles, UserGroups, middleware
│   ├── agents/                    # AgentAvailability (+last_seen presence heartbeat, is_assignable gate) + CustomAgentStatus + presence.py + reap-stale-presence task + load-fairness email picker
│   ├── analytics/                 # Reports, dashboard widgets, exports, calendar events
│   ├── api_keys/                  # APIKey model + auth class + viewset + per-key throttle + rate-limit-headers middleware + drf-spectacular extension
│   ├── attachments/               # File uploads (polymorphic GenericFK)
│   ├── billing/                   # Stripe billing, plans, subscriptions, webhooks, decorators
│   ├── comments/                  # Comments + Mention + CommentRead + ActivityLog (audit, 34 actions incl. 8 EMAIL_* for Inbox Hub) + LIVE signals
│   ├── contacts/                  # Contacts, Companies, Accounts, Groups, ContactEvent (360°) + LIVE signals + context.py (build_contact_context, shared with Hub cockpit)
│   ├── crm/                       # Activity + Reminder (M2M contacts/tickets), lead/account scoring + LIVE signals
│   ├── custom_fields/             # EAV custom fields per tenant + sync signals
│   ├── inbound_email/             # SMTP+IMAP ingestion; forks on TenantSettings.inbox_hub_enabled → legacy ticket-create OR park in Inbox Hub
│   ├── inbox_hub/                 # Email-triage workspace: 8 models + services + RoutingEngine + AssignmentEngine + state machine + SLA task + 11 viewset actions (+context) + 4 config viewsets + access.py (group gate)
│   ├── kanban/                    # Visual boards, columns, polymorphic CardPosition; drags route through tickets service (full audit/feed/SLA)
│   ├── knowledge/                 # KB articles, categories, search, stale alerts, gap digest, allowed_groups M2M
│   ├── messaging/                 # Real-time conversations (WS); Conversation.source_group; attachments on messages (POST broadcast action)
│   ├── nav/                       # URL-only helper (BadgeCountView — 7 categories: tickets/calendar/messages/emails/reminders/knowledge/inbox_hub; effective_role; emails=personal-inbox, messages=unread-chat)
│   ├── newsfeed/                  # Internal announcements, reactions, read receipts + LIVE signals
│   ├── notes/                     # Personal sticky notes (6 colors, pinning)
│   ├── notifications/             # In-app + email notifications + WebSocket (20 NotificationType values incl. 5 HUB_EMAIL_* — now all emitted)
│   ├── tenants/                   # Tenant model, middleware, frontend views, frontend_urls (35 paths), live broadcast layer, palette; LiveEventConsumer stamps presence on heartbeat
│   ├── tickets/                   # Core ticketing; Queue gains optional department FK (Inbox Hub); access.py (shared agent-visibility helper); SLA + business hours, CSAT, pipelines, macros, webhooks, deals
│   └── voip/                      # Asterisk ARI integration, SIP softphone, call logs, recordings, queues
├── main/                          # Django project root
│   ├── settings/{__init__,base,dev,prod}.py  # __init__ branches on DJANGO_DEBUG; base.py holds CELERY_BEAT_SCHEDULE + AGENT_PRESENCE_* + HUB_SLA_WARNING_MINUTES
│   ├── admin.py                   # SuperuserOnlyAdminSite + TenantFilteredAdmin mixin (full add/change/save with tenant picker)
│   ├── celery.py                  # Celery app + queue routing (7 globs + default), autodiscover_tasks() — NO beat schedule (lives in base.py)
│   ├── asgi.py                    # ProtocolTypeRouter: HTTP + WebSocket (6 consumer endpoints)
│   ├── context.py                 # contextvars-based tenant context (async-safe)
│   ├── models.py                  # TimestampedModel, TenantScopedModel
│   ├── managers.py                # TenantQuerySet, TenantAwareManager, SoftDeleteTenantManager
│   └── urls.py                    # 23 /api/v1/ includes (22 unique URLConfs; inbound-email dual-mounted at emails/) + /api/docs/
├── templates/                     # 48 .html files (18 subfolders under pages/)
│   ├── base.html                  # 265 lines — palette <style>, toast container, live-bus + live-connection JS, Flatpickr 3-CDN loader, sidebar-collapse FOUC fix
│   ├── includes/                  # 6 files — navbar, sidebar (Inbox Hub entry + avatar bg-image), softphone, messages, kb_sidebar_widget (orphan), page_back_button (11 sidebar paths incl. /inbox-hub/)
│   ├── pages/                     # 18 subfolders + 8 root html files (api_quickstart, calendar, dashboard, landing, login, profile, register, 403)
│   ├── landing/landing_crm.html   # Standalone marketing page (1,393 LOC; doesn't extend base.html)
│   └── {auth,knowledge,notifications,tickets}/email/  # 6 transactional email pairs
├── static/
│   ├── css/custom-v15.css         # 24,934 LOC (live file referenced by base.html — Crimson Black v9)
│   ├── css/custom.css             # 20,431 LOC (committed snapshot — NOT loaded; allowlisted in theme check)
│   ├── images/                    # Logo, favicon, hero artwork
│   └── js/                        # 14 vanilla-JS modules (5,262 LOC, incl. live-bus.js + live-connection.js + inbox-hub.js 1,177)
├── tests/                         # 56 root pytest modules + 7 app-level (63 total) + tests/base.py legacy scaffold
├── conftest.py                    # 16 factories + 20 fixtures (3 autouse: celery_eager, free_plan, clear_tenant_context)
├── pytest.ini                     # DJANGO_SETTINGS_MODULE=main.settings; pythonpath=. (3 lines, no asyncio_mode set)
├── requirements/{base,dev,prod}.txt   # prod.txt is literally `-r base.txt` — no extras; base.txt ~30 requirement lines
├── requirements.txt               # ROOT — byte-identical duplicate of requirements/base.txt
├── ecosystem.config.js            # PM2 prod: 5 processes
├── ecosystem.dev.config.js        # PM2 dev: 4 processes (no SMTP, watch-mode reloads)
├── Makefile                       # 33 targets (logs-django declared in .PHONY but no rule body — calling it errors)
├── docs/                          # README + architecture.md (stale 2026-02-06) + reference/{4 docs} (regen 2026-05-22 @ ea87bb2 — all stale vs Inbox Hub) + ui-consistency-audit.md
├── tmp/emails/                    # Dev email capture (filebased EmailBackend, gitignored)
├── logs/                          # PM2 log files — ~74MB (gitignored; celery-worker-error 40M, celery-beat-error 15M, django 14M, django-error 5.7M, no rotation)
├── media/                         # User-uploaded: tenants/{id}/… and inbound_emails/{id}/…
├── scripts/                       # check_theme.py + .theme_baseline.json (147 hex literals across 11 files)
├── db.sqlite3                     # Dev database (~12MB, gitignored)
├── celerybeat-schedule            # Celery Beat shelve file (built-in scheduler — django-celery-beat removed for Django 6 compat)
└── .env                           # 26 keys (.env.example covers only 16; 23 read-but-undocumented vars)
```

## Multi-Tenancy Architecture

### Three-Layer Isolation

1. **TenantMiddleware** (`apps/tenants/middleware.py`): Resolves tenant from subdomain (`{slug}.localhost` / `{slug}.{BASE_DOMAIN}`), or `TenantSettings.domain` for custom domains. Sets `request.tenant` and binds context. **`EXEMPT_PATH_PREFIXES`** (16 entries): `/static/`, `/media/`, `/api/v1/accounts/auth/`, `/api/v1/billing/plans/`, `/api/v1/billing/webhook/`, `/api/v1/tickets/csat/`, `/api/docs/`, `/api/schema/`, `/accounts/`, `/inbound/email/`, `/login/`, `/register/`, `/logout/`, `/verify-email/`, `/verify-email-sent/`, `/setup-company/`, `/workspaces/`. **`/admin/` is NOT in the exempt list** — it has a dedicated branch ([middleware.py:119-144](apps/tenants/middleware.py#L119)) that resolves the tenant from the subdomain when present, so a superuser on `straat-x.localhost:8001/admin/` gets `request.tenant=<Straat-X>` and `TenantScopedModel.save()` succeeds in admin creates. Bare `localhost:8001/admin/` still has `request.tenant=None`. **`/auth/handoff/` intentionally NOT exempt** — it must resolve the current tenant to verify membership.

2. **TenantAwareManager** (`main/managers.py`): Default `objects` manager auto-filters by `get_current_tenant()`. Returns **empty queryset** when no tenant in context. Use `Model.unscoped` for cross-tenant queries.

3. **TenantScopedModel** (`main/models.py`): Abstract base. UUID PK + Timestamped + `tenant` FK (CASCADE, editable=False, db_index=True). `objects = TenantAwareManager()`, `unscoped = models.Manager()`. Overridden `save()` auto-assigns `tenant` from context; raises `ValueError` if no tenant is bound and none provided. `SoftDeleteTenantManager` adds `is_deleted=False` filter on top.

### Async-Safe Tenant Context (`main/context.py`)

```python
set_current_tenant(tenant); get_current_tenant(); clear_current_tenant()
with tenant_context(tenant): ...  # context-manager form (preferred for tasks)
```

Uses `contextvars.ContextVar` named `"current_tenant"` — safe across asyncio tasks and Channels consumers.

### Superuser Admin Lock + Tenant-Filtered Mixin (`main/admin.py`, 106 lines)

- **`SuperuserOnlyAdminSite`** replaces `admin.site.__class__`; non-superusers get 403 on `/admin/` regardless of `is_staff`. Override of `has_permission(request)`: `request.user.is_active and request.user.is_superuser`.
- **`TenantFilteredAdmin` mixin** — drop-in for any `ModelAdmin` of a `TenantScopedModel`. Three responsibilities: `get_queryset` uses `model.unscoped.all()` then filters by `request.tenant`; `get_form` injects a `tenant = forms.ModelChoiceField` into the add/change form (because `TenantScopedModel.tenant` is `editable=False`); `save_model` backfills `obj.tenant` from form pick or `request.tenant` so the no-context guard in `TenantScopedModel.save()` doesn't trip. Guards against Django's recursive `_get_form_for_get_fields` discovery pass.

`main/admin.py` does NOT register any models — exports the mixin and replaces the admin site class.

## Live Broadcast Layer (committed)

Unified pub/sub real-time layer: a per-tenant WebSocket fans server-side mutations to a client-side `LiveBus`. Coexists with per-domain consumers (chat, notifications, ticket-feed, presence, voip).

### Backend

- **`apps/tenants/live.py::broadcast_live_event(tenant, event, payload, *, immediate=False)`**. `tenant` may be a model instance OR a raw pk. Group: `live_tenant_{pk}`. Wire shape: `{type:"live_event", event:"<domain>.<verb>", payload:{...}, ts:ISO8601}`. Defers via `transaction.on_commit` (unless `immediate=True`); swallows `_send` exceptions (best-effort).
- **`apps/tenants/consumers.py::LiveEventConsumer`** (`GROUP_PREFIX="live_tenant"`). Anonymous → close 4001. No tenant → close 4001. Non-member → close 4003 (membership verified even for valid JWTs). **Phase 1B**: on `connect` it stamps agent presence (`is_connect=True`), and on each inbound `{action:"ping"}` heartbeat it re-stamps presence (`is_connect=False`) and replies `{type:"live.pong"}`. Presence is intentionally NOT cleared on `disconnect` (avoids tab-refresh flapping) — the reaper task ages stale sessions out.
- **`apps/tenants/routing.py`** → `re_path(r"ws/live/$", LiveEventConsumer.as_asgi())`.

### Signal Emitters

| App | Receivers | Verbs |
|-----|-----------|-------|
| `accounts` | `TenantMembership.post_save/delete`, `Profile.post_save`, `User.post_save` (fans across every active membership; emits `avatar` URL in payload) | `membership.created/updated/deleted`, `profile.created/updated`, `user.updated` |
| `comments` | `Comment.post_save/delete` | `comment.created/updated/deleted` (payload `content_type="app_label.model"`, `object_id`) |
| `contacts` | `Contact/Company/Account/ContactGroup × post_save/delete` (ContactEvent intentionally skipped) | `contact.*`, `company.*`, `account.*`, `contact_group.*` |
| `crm` | `Activity/Reminder × post_save/delete`. Reminder verb resolved by state | `activity.*`, `reminder.created/updated/completed/cancelled/deleted` |
| `newsfeed` | `NewsPost.post_save/delete`, `NewsPostReaction.post_save/delete` | `newsfeed.created/updated/deleted`, `newsfeed.reacted` |
| `inbox_hub` (services/routing/assignment-emitted, NOT signal-emitted) | `park_email_in_hub`, routing/assignment/transition/escalate/reassign/note/convert/dismiss service fns | `hub_email.created/transitioned/assigned/reassigned/escalated/converted_to_ticket/dismissed` (all 7 now emitted) |
| `agents`/`tenants` (presence) | `presence.handle_live_heartbeat`, `reap_stale_presence` task | `agent.presence` (broadcast `immediate=True`) |

**Tickets do NOT server-side broadcast to `live_tenant_*`** — `apps/tickets/services.py::broadcast_ticket_event` publishes only to `ticket_feed_{tenant_id}`. The bridge to LiveBus is **client-side** in `static/js/ticket-feed.js`.

App configs (`apps/{accounts,comments,contacts,crm,newsfeed}/apps.py`) import their `signals` module in `ready()`. **`apps/inbox_hub/apps.py` has NO `ready()`** — no `signals.py` exists; the Hub emits LiveBus events imperatively from its service/routing/assignment functions.

### Frontend

- **`static/js/live-bus.js`** (175 LOC) — global `window.LiveBus`. API: `on/onMany/publish/debounce/rafBatch/isConnected/setChannelState`. Wildcard `"*"` subscriber gets all events. Cross-tab fan-out via optional `BroadcastChannel('kanzan-live')`. Handler errors caught + logged.
- **`static/js/live-connection.js`** (206 LOC) — global `window.LiveConnection`. Single shared `wss?://host/ws/live/`. Skips pre-auth pages and pages without a Django `sessionid` cookie. Exponential backoff 1s→30s with ±20% jitter, infinite retries. **25s heartbeat `{action:"ping"}`** with 8s pong timeout — this ping is also what drives server-side agent-presence stamping. Reconnect → publishes `live.reconnected`. Visibility-hook: regaining focus while closed forces immediate reconnect.
- **Wiring in `templates/base.html`** — load order: Bootstrap → DOMPurify → live-bus.js (always) → api.js → app.js → command-palette.js → custom-select.js → conditional on `tenant and user.is_authenticated`: live-connection.js → agent-availability.js → notes-panel.js → keyboard-shortcuts.js → ticket-feed.js → (if `voip_enabled`) SIP.js CDN + voip-softphone.js. **`inbox-hub.js` is page-specific** — loaded only by `templates/pages/inbox_hub/list.html` via `{% block extra_js %}`.
- **Live-status pill in navbar** — `#liveStatusPill` surfaced by `app.js::initLiveStatusPill()` when any tracked channel (`live`, `notifications`, `ticket_feed`) was previously open and is now reconnecting/closed.
- **`app.js::initSidebarUserLive`**: subscribes to `user.updated`, filters by `data-current-user-id` on `.sidebar-user`. Sets `style.backgroundImage = 'url("…")'` + `.has-image` class when `payload.avatar` is present (with embedded-quote escaping). Falls back to text initial otherwise.
- **`agent-availability.js::subscribePresence()`** (Phase 1B): **subscribes** to server-pushed `agent.presence` (filtered to current user) and reflects builtin-status changes in the navbar pill without clobbering a chosen custom status. It does NOT send heartbeats — that's `live-connection.js`'s 25s ping.
- **`ticket-feed.js`** continues to own `ws/tickets/feed/` and `LiveBus.publish('ticket.<verb>', …)` for sidebar/dashboard subscribers. Banner click → `ticket.show_pending {count}`. **Tenant-wide `ticket_assigned` Toast was removed**.

### Event Naming

`<domain>.<verb>` — domains: `user`, `membership`, `profile`, `comment`, `contact`, `company`, `account`, `contact_group`, `activity`, `reminder`, `newsfeed`, `ticket` (client-side normalisation), `notification`, `live`, `livebus`, **`hub_email`** (created/assigned/reassigned/transitioned/escalated/converted_to_ticket/dismissed — **all 7 now emitted server-side**), **`agent.presence`** (NEW Phase 1B; immediate broadcast on status change).

### Frontend subscribers (where each event drives UI)

| Page | Events | Handler |
|------|--------|---------|
| `dashboard.html` | `ticket.event`, `notification.received`, `newsfeed.*`, `live.reconnected` | Debounced 600ms refresh of stats + recent activity |
| `tickets/list.html` | `ticket.created`, `ticket.updated/.assigned/.closed/.deleted`, `ticket.show_pending` | Debounced reload (page 1 only) |
| `tickets/detail.html` | `comment.*` (filtered), `ticket.updated/.assigned/.closed`, `ticket.deleted`, `live.reconnected` | Refetch ticket/comments/activity |
| `contacts/list.html` | `contact.*`, `company.*`, `account.*`, `contact_group.*`, `live.reconnected` | Debounced 500ms list reload |
| `reminders/list.html` | `reminder.created/updated/completed/cancelled/deleted`, `live.reconnected` | Debounced 500ms refetch |
| `inbox_hub/list.html` | 7 `hub_email.*` events, `live.reconnected` | Debounced 400ms list+counts refresh (detail-pane refresh was removed — triaged mail leaves the backlog) |
| `app.js` (global) | `user.updated`, `livebus.channel_state`, 7 `hub_email.*` events, `agent.presence` | Sidebar live updates; live-status pill; **inbox_hub badge refetch (debounced 500ms)**; navbar status pill |

All page subscribers use a `document.visibilityState !== "hidden"` guard + `visibilitychange` listener.

### Channel-Layer Groups

- `live_tenant_{tenant_id}` — primary live events (newsfeed, CRM, memberships, contacts, comments, **hub_email**, **agent.presence**)
- `notifications_{user_id}` — in-app notifications
- `chat_{conversation_id}` — chat messages (with attachments)
- `ticket_feed_{tenant_id}` — ticket lifecycle (client republishes into LiveBus)
- `ticket_{ticket_id}_presence` — agent presence on a ticket
- `voip_{tenant_id}` — call state

### Close codes

| Code | Where | Reason |
|------|-------|--------|
| `1000` | `live-connection.js` | Manual clean close on `beforeunload` |
| `4001` | `ChatConsumer`, `TicketPresenceConsumer`, `TicketListConsumer`, `LiveEventConsumer` | Anonymous user or missing tenant |
| `4002` | `ChatConsumer` | Invalid `conversation_id` UUID |
| `4003` | presence/list/live consumers | Non-tenant-member |
| `4004` | `ChatConsumer` | Conversation belongs to different tenant than Host header |

## Messaging Attachments (committed)

- **`MessageCreateSerializer.body`** is `CharField(allow_blank=True, required=False, default="")`. Attachment-only message valid serializer-side; the frontend must block fully-empty send.
- **`MessageSerializer.attachments`** is a `SerializerMethodField`. Uses `_prefetched_attachments` if set, else GenericFK lookup.
- **`MessageViewSet.broadcast`** — `POST /api/v1/messaging/conversations/{conv}/messages/{msg}/broadcast/`. **Author-only.** Re-emits the message over `chat_{conv_id}` group after client links attachments. `_broadcast_message` is a `@classmethod` that calls `cls._build_attachment_payload(message)` and includes `attachments` + `author_name` (fallback email if name blank).
- **`ChatConsumer._create_message`** includes `"attachments": []` in inbound-sent payloads (forward-compat).
- **`templates/pages/messaging/chat.html`** adds a pending-attachments tray (`#pendingAttachments`, `#messageAttachInput`), `buildAttachmentBlock()` helper.

## Inbox Hub (Phase 0+1A committed @ `8682e80`; Phase 1B engine + triage-COCKPIT frontend + email-inbox handoff + group-gated access in working tree)

Email-to-Queue triage workspace. Reshapes inbound-email flow so NEW messages land in a centralised Hub for agent triage instead of auto-creating tickets, then routes them to a department, seeds SLA deadlines, and auto-assigns to an online agent. **Default: OFF** — the seam at `apps/inbound_email/services.py` (committed) forks on `TenantSettings.inbox_hub_enabled` (BoolField, default `False`).

> **Backend vs frontend split (working tree):** the *backend* is the full Phase 1B engine (routing, presence-aware assignment, hold/drain, state machine, SLA breach task, 10 viewset actions incl. claim/escalate/transition/note + a read-only `context` action). The *frontend* (`inbox-hub.js` 1,177 LOC + `inbox_hub/list.html`) is a **triage cockpit** that surfaces convert/assign/dismiss over the untriaged backlog (5 workload lenses) plus a per-email **customer-context card** — it does NOT call the claim/escalate/transition/note endpoints. The "real work" on an email happens after triage, either in the converted Ticket or in the assigned agent's Emails page (see §"Agent email-inbox handoff").
>
> **⚠️ Access is now GROUP-GATED (NEW Jun-9 refactor).** `apps/inbox_hub/access.py::can_access_inbox_hub(membership)` is the single source of truth: a member may use the Hub only if **Admin (`effective_role.hierarchy_level ≤ 10`) OR they belong to ≥1 `UserGroup` in the tenant.** Non-Admins in no group are fully locked out — hidden sidebar entry, zeroed badge, 403 on both the page (`_inbox_hub_access_required` decorator → `pages/403.html`) and the API (`HubEmailPermission` + `IsHubEmailAccessible`). This **replaced** the old department-based row-scoping entirely; admins control Hub access from the Groups page. Admins are exempt because the "one user per group per tenant" rule often prevents them being a member.

### The seam — `apps/inbound_email/services.py` (committed)

```python
if existing_ticket:
    _add_reply_to_ticket(inbound, existing_ticket, contact, system_user)
else:
    settings = getattr(tenant, "settings", None)
    if settings is not None and settings.inbox_hub_enabled:
        from apps.inbox_hub.services import park_email_in_hub
        park_email_in_hub(inbound, tenant, contact, system_user)
    else:
        _create_ticket_from_email(inbound, tenant, contact, system_user)
```

> **Variable-shadowing footgun**: the local `settings` rebinds the module-level `from django.conf import settings`. Sibling `resolve_tenant_from_address` is unaffected (separate function), but future code in `process_inbound_email` after the seam would silently get `TenantSettings | None`.

**Existing-thread reply** path always goes straight to the matching ticket regardless of the flag — the Hub triages NEW conversations only. `resolve_tenant_from_address` also has a **Strategy 4** (`settings.IMAP_DEFAULT_TENANT_SLUG` last-resort fallback) so shared mailboxes route somewhere instead of returning `None`.

### Models (8 — `apps/inbox_hub/models.py`, 431 LOC)

- **`Department(TenantScopedModel)`** — `name`, `slug` (UniqueConstraint per tenant `ih_department_tenant_slug_uniq`), `description`, `lead` (FK User PROTECT), `members` (M2M via `DepartmentMembership`), `default_queue` (FK `tickets.Queue` SET_NULL), `business_hours` (FK `tickets.BusinessHours` SET_NULL), `is_active`. Index `(tenant, is_active)`.
- **`DepartmentMembership(TenantScopedModel)`** — through-model. `department`, `user`, `skills` (JSON list — empty until skill-based routing). UniqueConstraint `(department, user)`.
- **`HubEmail(TenantScopedModel)`** — the workspace entity. `inbound` 1:1 to `InboundEmail` CASCADE (`related_name="hub_email"`), `contact`, `department`, `queue`, `assignee` (all db_indexed). **9-state enum** (`NEW → ASSIGNED → IN_PROGRESS → PENDING_AGENT ⇄ AWAITING_CUSTOMER → ESCALATED → RESOLVED → CONVERTED_TO_TICKET | DISMISSED`) + **4-priority enum** (`low/normal/high/urgent` — NO "medium"). SLA fields (`sla_response_due_at` db_indexed, `sla_resolution_due_at`, `response_breached`, `resolution_breached`, `first_responded_at`, `first_assigned_at`, `pause_started_at`, `total_pause_seconds`). Escalation: `escalation_count`, `escalated_to`. Terminal paths: `converted_ticket` (OneToOne to Ticket SET_NULL, `related_name="origin_hub_email"`), `dismissed_at`/`by`/`reason`. `auto_classification_data` JSONField (AI drop-zone; also used for `sla_warning_sent` dedup flag). **5 indexes**: 4 composite on `(tenant, …, state)` + one partial `ih_email_active_sla_due` on `(tenant, sla_response_due_at)` filtered to active states.
- **`HubEmailAssignment(TenantScopedModel)`** — immutable audit row. `Reason` enum (`AUTO/MANUAL/ESCALATION/REASSIGNMENT`). `assigned_to` PROTECT, `assigned_by` null=system.
- **`HubEmailNote(TenantScopedModel)`** — internal agent note (`ordering=["created_at"]` ASC — old notes first; intentionally NOT carried over on conversion). `author` PROTECT.
- **`HubEmailSLA(TenantScopedModel)`** — per-(queue, priority) or per-(department, priority) policy with `response_minutes`/`resolution_minutes`/`escalation_minutes`. Both FKs nullable; **conditional UniqueConstraints** scope uniqueness to non-null queue/department (Postgres native; SQLite ≥3.30).
- **`RoutingRule(TenantScopedModel)`** — ordered IF/THEN rule consulted by RoutingEngine. `match` JSON: `{sender_domain[], subject_regex, recipient_local[], keyword[]}` (keys AND, values OR). Outputs: `department`, `queue`, `category`, `priority`, `stop_on_match`.
- **`QueueRouting(TenantScopedModel)`** — 1:1 supplement to `tickets.Queue`. `strategy_code` (pipe-delimited fallback chain, default `"availability_aware|least_loaded|round_robin"`) + `leave_unassigned_when_no_match`.

### Phase 1B engine (working tree)

**`_post_park_hooks` is now FILLED** ([services.py]) — scheduled via `transaction.on_commit` from `park_email_in_hub`, each step try/except-isolated so a failure never breaks the inbound pipeline:
1. `RoutingEngine.classify_and_route(hub_email)` — resolve department/queue/category/priority.
2. `_initialize_hub_sla(hub_email)` — seed `sla_response_due_at`/`sla_resolution_due_at` from the matching `HubEmailSLA` (queue+priority → department+priority; no-op if none). **Wall-clock deadlines** (no business-hours math yet). Done BEFORE assignment.
3. `AssignmentEngine.try_assign(hub_email)` — only when `settings.inbox_hub_auto_assign` (default True).

**`apps/inbox_hub/routing.py` — RoutingEngine** (NOT a Channels route): `classify_and_route` recomputes routing fields from `RoutingRule.unscoped.filter(tenant, is_active=True).order_by("order","id")`. Match: keys AND-combined, values OR-combined; `sender_domain` exact-or-`.subdomain`; `keyword` substring over `subject\nbody`; `subject_regex` IGNORECASE (invalid regex → clause fails + warns). Empty `match` matches **nothing** (never a catch-all). Last-matched rule's non-null outputs win; `stop_on_match` breaks. Fallback department = `TenantSettings.inbox_hub_default_department` (if active) else the single active Department if exactly one exists; queue falls back to `department.default_queue`. Writes `EMAIL_CATEGORISED` (+`EMAIL_QUEUED`), broadcasts `hub_email.transitioned`.

**`apps/inbox_hub/assignment.py` — AssignmentEngine + hold/drain**: Strategies are **string tokens** (not classes), applied left-to-right as sort-key tie-breakers: `availability_aware` (most spare capacity first) → `least_loaded` (active HubEmail count + `current_ticket_count`) → `round_robin` (least-recently-assigned first). `DEFAULT_STRATEGY = "availability_aware|least_loaded|round_robin"`, overridable per-queue via `QueueRouting.strategy_code`.
- `try_assign(hub_email, *, actor=None)` — candidate pool = department members (or, if no department, tenant members at `hierarchy_level==30`), filtered to `is_assignable`, selected via the strategy chain, `assign_to(..., require_online=True)`. **If none online → "held"** (stays NEW/unassigned) and `_notify_hold` nudges the department lead.
- `assign_to(hub_email, user, *, reason, actor=None, require_online=False)` — atomic `select_for_update` on the HubEmail (concurrency guard: bails if already assigned or not NEW); locks `AgentAvailability` + re-checks `is_assignable` when online required; sets assignee, `first_assigned_at`, state→ASSIGNED; creates `HubEmailAssignment`; bumps `current_ticket_count`; writes `EMAIL_AGENT_ASSIGNED`; broadcasts `hub_email.assigned`.
- `drain_department_backlog(user, tenant)` — **"drain"**: on agent (re)connect, assign oldest held NEW/unassigned emails in their department(s) up to `remaining_capacity`. Called from `presence._maybe_drain`.

**Presence layer** (`apps/agents/presence.py` + `apps/agents/models.py` + `apps/agents/tasks.py`):
- `AgentAvailability.last_seen` (DateTimeField, db_indexed, migration `agents/0007`). Module const `DEFAULT_PRESENCE_TTL_SECONDS = 90`.
- `AgentAvailability.is_assignable` — the single gate the AssignmentEngine consults: `status == ONLINE` AND `remaining_capacity > 0` AND `presence_fresh` (last_seen within `settings.AGENT_PRESENCE_TTL_SECONDS`) AND (if `auto_away_outside_hours`) within working hours.
- `presence.touch_presence(user, tenant)` — `get_or_create` on `AgentAvailability.unscoped`; stamps `last_seen`; auto-promotes **OFFLINE→ONLINE only** when `AGENT_PRESENCE_AUTO_ONLINE` (never overrides manual AWAY/BUSY).
- `presence.handle_live_heartbeat(user, tenant, *, is_connect=False)` — sync entry called by `LiveEventConsumer`; stamps presence, broadcasts `agent.presence` on change, drains held backlog via `_maybe_drain` on any (re)connect or status change. Error-swallowing.
- `agents.tasks.reap_stale_presence` (`@shared_task`, default queue) — flips `ONLINE` rows whose `last_seen` is NULL or older than TTL to `AWAY`, broadcasts `agent.presence` per row, iterates `chunk_size=200`. **Beat: every 60s.**

**`apps/inbox_hub/state_machine.py`** — `ALLOWED_TRANSITIONS`, `can_transition(old,new)` (False if equal), `assert_transition` (raises `ValueError`):
```
NEW               → {ASSIGNED, ESCALATED, DISMISSED, CONVERTED_TO_TICKET}
ASSIGNED          → {NEW, IN_PROGRESS, PENDING_AGENT, AWAITING_CUSTOMER, ESCALATED, RESOLVED, DISMISSED, CONVERTED_TO_TICKET}
IN_PROGRESS       → {PENDING_AGENT, AWAITING_CUSTOMER, ESCALATED, RESOLVED, DISMISSED, CONVERTED_TO_TICKET}
PENDING_AGENT     → {IN_PROGRESS, AWAITING_CUSTOMER, ESCALATED, RESOLVED, DISMISSED, CONVERTED_TO_TICKET}
AWAITING_CUSTOMER → {IN_PROGRESS, PENDING_AGENT, ESCALATED, RESOLVED, DISMISSED, CONVERTED_TO_TICKET}
ESCALATED         → {ASSIGNED, IN_PROGRESS, RESOLVED, DISMISSED, CONVERTED_TO_TICKET}
RESOLVED          → {IN_PROGRESS, CONVERTED_TO_TICKET, DISMISSED}
CONVERTED_TO_TICKET → {}   DISMISSED → {}   (terminal)
```

**`apps/inbox_hub/tasks.py::check_hub_sla_breaches`** (`@shared_task`, default queue) — flags response breaches (auto-escalates via `escalate_hub_email`), fires a one-shot warning `HUB_SLA_WARNING_MINUTES` (default 15) before deadline (deduped via `auto_classification_data["sla_warning_sent"]`), flags resolution breaches. Cross-tenant (`.unscoped`). **Beat: every 120s.**

### Service layer (`apps/inbox_hub/services.py`, 545 LOC)

All write polymorphic `ActivityLog` rows and broadcast LiveBus on commit. **All 8 `EMAIL_*` actions and all 5 `HUB_EMAIL_*` Notifications are now emitted** (the prior doc's "no Notification creators" / "_post_park_hooks empty" claims are obsolete).
- `park_email_in_hub` — idempotent `get_or_create(inbound=…)`; sets `PARKED_IN_HUB`; `EMAIL_RECEIVED`; broadcasts `hub_email.created`; schedules `_post_park_hooks`.
- `convert_to_ticket(hub_email, actor, *, queue=None, status=None, assignee=None, priority=None)` — idempotent; reuses `_create_ticket_from_email` (returns the ticket); applies overrides; state→CONVERTED_TO_TICKET; `EMAIL_CONVERTED_TO_TICKET`; broadcasts (payload `ticket_id`/`ticket_number`).
- `dismiss_hub_email(hub_email, actor, reason="")` — idempotent; state→DISMISSED + dismissed_at/by/reason; `EMAIL_DISMISSED`.
- **NEW** `transition_hub_email(hub_email, new_state, actor=None, *, note="")` — `assert_transition`; `STATUS_CHANGED`; broadcast `hub_email.transitioned`.
- **NEW** `escalate_hub_email(hub_email, actor=None, *, reason="")` — `escalation_count += 1`, `escalated_to`=dept lead, state→ESCALATED if legal; `EMAIL_ESCALATED`; `_notify_escalation` (`HUB_EMAIL_ESCALATED_TO_ME`).
- `reassign_hub_email(hub_email, new_user, actor=None, *, reason="")` — `select_for_update`; online NOT required (manual override); adjusts both agents' counts; `EMAIL_REASSIGNED`/`EMAIL_AGENT_ASSIGNED`; `_notify_reassignment`. **Also stamps `inbound.assignee = new_user` + `inbox_status=PENDING` + `is_read=False`** (the agent email-inbox handoff — backs the `assign`/`reassign`/`claim` viewset actions).
- `add_hub_email_note(hub_email, author, body)` — creates `HubEmailNote`; broadcasts `hub_email.transitioned`. **Service exists but the cockpit frontend no longer calls it** (no `/note/` UI).

### API surface (`apps/inbox_hub/views.py` + `urls.py`, working tree)

`HubEmailViewSet` (list/retrieve only — `mixins.ListModelMixin + RetrieveModelMixin`) permission stack `[IsAuthenticated, IsTenantMember, HubEmailPermission, IsHubEmailAccessible]`; fresh `get_queryset` with `select_related("inbound","contact","department","queue","assignee","converted_ticket","escalated_to")` + `prefetch_related("notes","notes__author")`. **Agent-tier row filter (in the queryset):** `if membership.effective_role.hierarchy_level > 20: qs = qs.filter(Q(state=NEW) | Q(assignee=me))` — so a crafted `?state=` can't surface another agent's in-flight mail. Chips: `assignee=me|unassigned|<uuid>`, `state`/`priority`/`queue`/`department`, and **`sla_risk=true`** (rows with `sla_response_due_at IS NOT NULL AND response_breached=False` — the SLA-at-risk lens, paired client-side with `?ordering=sla_response_due_at`).

| Action | Method / URL | Codename | Service |
|---|---|---|---|
| `list`/`retrieve` | `GET /hub-emails/[{id}/]` | `hub_email.view` | — |
| **`context`** | `GET /{id}/context/` | `hub_email.view` | `contacts.context.build_contact_context` → `{contact, stats, recent_tickets}` (`{contact:None,...}` if no contact) — **NEW** triage-cockpit customer card |
| `convert_to_ticket` | `POST /{id}/convert-to-ticket/` | `hub_email.convert` | `convert_to_ticket` → `{ticket, hub_email}` 201 |
| `dismiss` | `POST /{id}/dismiss/` | `hub_email.dismiss` | `dismiss_hub_email` |
| `assign` | `POST /{id}/assign/` | `hub_email.assign` | `reassign_hub_email` (validates member via `_require_member`) |
| `reassign` | `POST /{id}/reassign/` | `hub_email.reassign` | `reassign_hub_email` |
| `claim` | `POST /{id}/claim/` | **none** — agent-level (≤30) | `reassign_hub_email(self)` |
| `escalate` | `POST /{id}/escalate/` | `hub_email.escalate` | `escalate_hub_email` |
| `transition` | `POST /{id}/transition/` | **none** — agent-level (≤30) | `transition_hub_email` (`ValueError`→400) |
| `note` | `POST /{id}/note/` | `hub_email.note` | `add_hub_email_note` → 201 |

**No `reply` action** despite the `hub_email.reply` codename being seeded. **4 config viewsets** (all `ModelViewSet`, registered in `urls.py`): `DepartmentViewSet` (+`add-members`/`remove-members`; list/retrieve open to members, writes `IsTenantAdminOrManager`), `RoutingRuleViewSet` (+`reorder`, manager-gated), `HubEmailSLAViewSet`, `QueueRoutingViewSet` (manager-gated).

**`HubEmailPermission`** (`apps/inbox_hub/permissions.py`) — a **local** permission class replacing the global `HasTenantPermission`/`ACTION_MAP`, because action names `assign`/`escalate` collide with `TicketViewSet` (mapped to `update`). Uses `effective_role`. **First gate = the group access gate**: `if level > 10 and not user_in_any_group(user, tenant): return False`. Then `ACTION_CODENAMES` per-action map (incl. `context → hub_email.view`); `AGENT_LEVEL_ACTIONS = {claim, transition}` gated `≤ 30` with no codename; when the role carries explicit perms it checks the codename, else hierarchy fallback: `view ≤40`, `convert/reply/escalate/note ≤30`, `assign/reassign/dismiss ≤20`. **`IsHubEmailAccessible` row-scoping** (object-level, `effective_role`): `≤10` unrestricted; **non-Admin must be `user_in_any_group` (defence-in-depth)**; `≤20` all rows; agent-tier (`>20`) → `state=NEW OR assignee=me`. **All department-based scoping (the old `department_memberships ∪ led_departments` + safety-valve) is GONE** — access is now group-gated, row-scope is state/assignee only.

### Frontend — TRIAGE COCKPIT (working tree; rewritten again)

⚠️ **The Hub frontend grew from "triage desk" (3 priority filters) into a triage cockpit (5 workload lenses + a customer-context card).** It still does NOT call claim/escalate/transition/note (those remain backend-only).

- **`templates/pages/inbox_hub/list.html`** (**270 LOC**) + **`static/js/inbox-hub.js`** (**1,177 LOC**, cache-bust **`?v=8`**, vanilla IIFE). Untriaged-first; `document.body.classList.add('ih-page')` → full-viewport surface.
- **Left rail = 5 workload LENSES** (`state.activeLens`), NOT priority filters: **`all`** (state=new) / **`unassigned`** (state=new & unassigned) / **`mine`** (assignee=me, any state) / **`oldest`** (state=new, created_at asc) / **`sla`** (`sla_risk=true`, ordered by `sla_response_due_at`). The **SLA lens self-hides** when its count is 0 (default tenants seed no SLA policies) and falls back to `all`. Counts = 4 parallel HEAD requests (`all/unassigned/mine/sla`).
- **NEW customer-context card** (`#ihContextCard`, biggest addition) — selecting a row fires `loadContextFor(row)` in parallel with the body load, hitting **`GET /api/v1/inbox-hub/hub-emails/{id}/context/`** (deliberately the Hub action, NOT `/contacts/.../context/` which row-scopes agents out of a parked contact). Renders known-vs-new badge, company/MRR/health, bounce warning, a stats strip (total/open/CSAT/last ticket), and clickable recent-ticket rows. Per-session `contextCache` (keyed by contactId) + a `contextReqId` token to drop stale paints during fast J/K nav. Unknown senders → minimal "First contact" card.
- **SLA badge** in the detail header (`renderSlaBadge`) — only when a response deadline exists; tone info→warning→danger (≤60min / ≤15min / overdue). **Open-ticket nudge** (`renderOpenTicketNudge`) — "⚠ N open tickets" linking to the latest, to discourage duplicate tickets.
- **Three triage actions**: **convert** / **assign** / **dismiss**. Convert modal gained an assignee dropdown (`assignee_id`) + priority `medium`→`normal` fix; 8s post-convert auto-redirect → toast. The `/claim/`, `/escalate/`, `/transition/`, `/note/` endpoints are NOT called.
- **Floating Assign menu** — `openAssignMenu()`/`positionMenu()` build a `position:fixed` menu portaled to `<body>` (escapes `overflow:hidden`, viewport flip-up/clamp), listing "Assign to me" + other agents (cached `/api/v1/accounts/users/`); posts `POST /{id}/assign/ {assignee_id}`. Closes on outside-click / Esc / resize / scroll.
- **Keybinds**: J/K navigate, C convert, **A assign**, X dismiss, Esc close. `afterTriage()` resets the pane + refreshes list/counts (the email left the backlog). Body via strict `BODY_SANITIZE_CONFIG` DOMPurify; all customer text via `textContent`/`createElement`.
- **List serializer is a "cockpit" projection** (`_HubEmailBaseSerializer`): adds `contact_id` (null for unknown sender), an HTML-stripped 140-char `snippet` (`strip_tags` + `Truncator`), `has_attachments`/`attachment_count`, and **promotes** `sla_response_due_at`/`response_breached`/`first_responded_at` onto the list row (for the badge + SLA lens). Detail serializer adds the nested inbound body + notes + escalation/resolution SLA fields.
- **LiveBus subs**: 7 `hub_email.*` + `live.reconnected`, debounced 400ms → list + counts only.
- **Frontend URL** `frontend_urls.py`: `path("inbox-hub/", views.inbox_hub_page)` — now gated by **`@_inbox_hub_access_required`** (login + active membership + `can_access_inbox_hub`; denies → `pages/403.html` "limited to members of a group"). Sidebar entry is wrapped in `{% if can_access_inbox_hub %}` (FIRST link in "Inbox" section, `#sidebarBadgeInboxHub`). `page_back_button.html` includes `/inbox-hub/`.
- **`apps/nav/views.py::BadgeCountView._inbox_hub_count(tenant, user, membership)`** — returns 0 if `not can_access_inbox_hub(membership)`; else counts only `HubEmail.unscoped.filter(tenant, state=NEW)` tenant-wide. (Was: 5-state active set narrowed per-agent.)
- **CSS** `custom-v15.css` — the Hub section was rewritten for the cockpit: added a full **`.ih-context-*` customer-card** block (head/avatar/id/name/badge/meta/stats/tickets/`.ih-warn`/`.ih-tkt-*`), `.ih-sla-badge--{info,warning,danger}`, `.ih-row-avatar--{known,new}`, `.ih-row-main/-snippet/-clip`, `.ih-nav-foot`, plus the carried-over `.ih-actionbar`/`.ih-btn-caret`/floating `.ih-menu*` (`@keyframes ihMenuIn` + reduced-motion guard) and `body.ih-page` grid. **Zero new hex** — all `var(--crm-*)`/`var(--status-*)`.

### Agent email-inbox handoff (`InboundEmail.assignee` — NEW working tree)

When a HubEmail is **manually** assigned, the original customer message is handed to that agent's personal Emails page. This is how an agent actually "works" a triaged email.

- **`InboundEmail.assignee`** FK (SET_NULL, db_indexed, `related_name="assigned_inbound_emails"`) + index `email_tenant_assignee_idx (tenant, assignee, inbox_status)` — migration **`inbound_email/0010` (untracked)**. `assignee` is NOT immutable (unlike `linked_*`/`actioned_*`).
- **Set only by `reassign_hub_email`** (`apps/inbox_hub/services.py`): on `assign`/`reassign`/`claim`, it sets `inbound.assignee = new_user`, `inbox_status = PENDING`, `is_read = False`. ⚠️ **`AssignmentEngine.assign_to` (the auto-assign path) does NOT touch `inbound.assignee`** — only `he.assignee`. So auto-assigned mail stays in the Hub; only manual claim/assign/reassign reaches the agent's email inbox.
- **Consumer side — `apps/inbound_email/api_views.py`**: `InboundEmailViewSet.get_queryset` gains query-param branches — `?assigned=me` (`filter(assignee=request.user)`, bypasses the internal/customer split + bounce-hiding), `?internal=true` (excludes `sender_type=CUSTOMER`), `?mine=true` (`recipient_email__iexact=user.email`, drops OUTBOUND/SYSTEM noise). New **`create_ticket` action** `POST /api/v1/inbound-email/{id}/create-ticket/` (`get_permissions` swaps to `[IsAuthenticated, IsTenantMember]`, handler enforces `effective_role ≤ 30`; idempotent → 400 if `ticket_id` set): if `email.hub_email` exists → `inbox_hub.services.convert_to_ticket`; else legacy `_create_ticket_from_email`. `serializers.py` adds `assignee`/`assignee_name` (via `_AssigneeNameMixin`) to both list + detail serializers.
- **`templates/pages/emails/list.html`** (**923 LOC**): new **"Assigned to me" stat tab** (`#emailCountAssigned`); dual-source load = `Promise.all` of `?internal=true&mine=true` + `?assigned=me`, merged/deduped by id; new **"Create ticket"** button (`#previewCreateTicketBtn`) in the unlinked-email preview → the `create-ticket` action (shown only when `_assigned || assignee || sender_type==='customer'`).
- The reassign Notification deep-links to `/emails/` (vs `/inbox-hub/` for assignment/escalation/SLA notifications).

### RBAC (12 codenames — `apps/accounts/defaults.py` + migration `accounts/0012`) + group gate

- `hub_email.{view, assign, reassign, convert, dismiss, reply, escalate, note}` (8) + `department.{view, manage}` (2) + `routing_rule.manage` (1) + `hub_sla.manage` (1).
- Grants: Admin/Manager = all 12; Team Lead = agent-tier (`view/convert/reply/escalate/note` + `department.view`) **plus** `assign/reassign/dismiss`; Agent/IT/HR = agent-tier only; Viewer = `view` via ≤40 fallback only.
- `accounts/0012_seed_inbox_hub_permissions` uses `role.permissions.add(*perms)` (not `.set()`) so operator customisations are preserved. Reversible.
- ⚠️ **The codename grants are now SUBORDINATE to the group access gate** (`apps/inbox_hub/access.py`, NEW). Even a Manager with all 12 codenames is denied the Hub entirely (403 + zeroed badge + hidden nav) unless they are an Admin OR a member of ≥1 `UserGroup`. Admins (`≤10`) bypass the gate. This is a sharp behavioral shift — existing Managers/Team-Leads with no group membership are locked out until added to a group.

### Configuration & seeding

- **`TenantSettings`** new fields (migration `tenants/0010`): `inbox_hub_auto_assign` (BoolField, default **True** — toggles auto-assign + hold/drain; False = manual claim only); `inbox_hub_default_department` (FK `inbox_hub.Department`, SET_NULL, routing fallback). (`inbox_hub_enabled`, default False, came earlier in `tenants/0009`.) All 3 exposed in serializer + Manager write allowlist. Two settings-UI toggles in `settings/tenant.html` (`#inboxHubEnabledToggle`, `#inboxHubAutoAssignToggle`).
- **Settings constants** (`main/settings/base.py`, env-overridable): `AGENT_PRESENCE_TTL_SECONDS=90`, `AGENT_PRESENCE_AUTO_ONLINE=True`, `HUB_SLA_WARNING_MINUTES=15`.
- **`manage.py seed_inbox_hub_defaults [--tenant-slug <slug> | --all-tenants]`** — seeds **one "General" Department** (lead = lowest-hierarchy active member; default queue = "General"/"Support" else oldest; enrolls active non-viewer members), points `TenantSettings.inbox_hub_default_department` at it. Idempotent. **Does NOT seed** RoutingRules/HubEmailSLA/QueueRouting.

### Tests (verified run 2026-06-10)

- **`tests/test_inbox_hub.py`** (**21**, was 13) — Phase 1A services/RBAC PLUS new: `TestCockpitSerializer` (cockpit row fields — snippet HTML-strip/truncate, `contact_id` null for unknown sender), `TestHubEmailContextAction` (the `context` action; agent can reach Hub-context but NOT contacts-context), `TestSlaRiskLens` (`sla_risk` filter+order), and **group-gated listing** (`test_manager_in_group_can_list` / `test_manager_without_group_denied`).
- **`tests/test_inbox_hub_routing_assignment.py`** (**38**, was 25/27) — presence `is_assignable` + `reap_stale_presence`; RoutingEngine; AssignmentEngine (assign/hold/load-balance/department-scope) + drain; end-to-end `park_email_in_hub`; HTTP action API + config-viewset RBAC.
- **`tests/test_inbound_email.py`** (14) — incl. `TestEmailsInternalPersonalScope` (3 funcs, `?internal=true&mine=true`). Still no `?assigned=me`/`create_ticket` coverage.
- **`tests/test_access_control.py`** (**12**, +1) — new `test_agent_created_ticket_hidden_after_assigned_to_other` documents the `tickets/access.py` tightening (creator loses visibility on handoff).
- **Result:** inbox_hub 21 + routing_assignment 38 = **59 passed**; inbound_email 14 + access_control 12 = **26 passed**. (Separately, `test_badges.py` 36/2 — 2 stale fails from the `_message_count` comment→chat repurpose.)

### Remaining future scope / known gaps

- **Auto-assign ≠ email handoff**: `AssignmentEngine.assign_to` sets only `HubEmail.assignee`, never `InboundEmail.assignee`, so auto-assigned mail does not appear in the agent's Emails page (only manual claim/assign/reassign do).
- **Dead-but-present surface**: the backend `claim`/`escalate`/`transition`/`note` viewset actions + the `hub_email.reply` codename are all live server-side but unused by the cockpit frontend.
- **`first_responded_at` read-but-never-written**: `tasks.py::check_hub_sla_breaches` guards the response-breach branch on `first_responded_at is None`, but no engine code ever WRITES `first_responded_at` — so a parked email is never marked first-responded and the response-breach always fires on the deadline. Latent gap.
- **`escalate_hub_email` increments `escalation_count` even when the ESCALATED transition is illegal** (e.g. from RESOLVED) — only the state change is silently skipped. `HubEmailAssignment.Reason.ESCALATION` is a seeded-but-never-emitted enum value (escalation creates no assignment row).
- **Seeded-but-unused fields**: `DepartmentMembership.skills`, `HubEmail.tags` (surfaced in the serializer but never written), `QueueRouting.leave_unassigned_when_no_match`, `HubEmail.pause_started_at`/`total_pause_seconds` (Hub SLA is pure wall-clock), `HubEmailSLA.escalation_minutes` (only response/resolution used).
- **Stale module docstrings**: `inbox_hub/urls.py` ("Phase 1A only registers the HubEmail viewset" — all 5 are registered), `inbox_hub/services.py` ("RoutingEngine/AssignmentEngine … out of scope for Phase 1A"), and `inbox_hub/serializers.py` ("Phase 1A surface").
- Still future: **business-hours-aware** SLA (currently wall-clock); `auto_classification_data` AI classification; backfill of historical `InboundEmail` when the flag flips ON; auto-seed of a default Department on tenant creation (still manual via `seed_inbox_hub_defaults`); RoutingRule/HubEmailSLA/QueueRouting seeding; test coverage for `?assigned=me` + `create_ticket`.

## Models (91 model classes across 21 apps with models.py — `nav` is URL-only)

> Counted as `class X(<…>)` in `apps/*/models.py`, excluding `TextChoices/IntegerChoices`, `Manager`, `QuerySet`. Raw `^class` grep gives 102.

### Base Models (Abstract)
- **TimestampedModel**: UUID PK + `created_at` + `updated_at`; default ordering `["-created_at"]`.
- **TenantScopedModel**: TimestampedModel + `tenant` FK (CASCADE, editable=False, db_index=True) + auto-filtering.

### Tenants / Accounts

**tenants** (2): `Tenant` (name, slug unique, domain unique nullable, is_active, logo); `TenantSettings` (1:1; auth_method, SSO config, timezone, date_format, branding `primary_color`+`accent_color` with hex validators, `inbound_email_address`, business hours/days, `auto_close_days` (5), `csat_delay_minutes` (60), `auto_transition_on_assign`, `auto_send_ticket_created_email`, `auto_assign_inbound_email_tickets`, **`inbox_hub_enabled` (default False, mig 0009)**, **`inbox_hub_auto_assign` (default True, mig 0010)**, **`inbox_hub_default_department` (FK, mig 0010)**). Defaults: `primary_color="#6366F1"`, `accent_color="#F59E0B"`. Crimson Black `#C1121F`/`#E11D2D` is only the fallback in `apps/tenants/colors.py::derive_palette`.

**accounts** (8): `User(AbstractUser)` (email-based, UUID PK, `auth_version`, `avatar`, `phone`, `username=None`, `is_service_account`). `Permission` — **global** (not tenant-scoped); `Action` enum (7: view/create/update/delete/assign/export/manage). `Role(TenantScopedModel)` — M2M `permissions`, `hierarchy_level` default 100, `is_system`. `Profile(TenantScopedModel)`. `TenantMembership` — NOT TenantScoped; UUID PK; FKs `user`, `tenant`, `role` (PROTECT), `temporary_role`, `temporary_role_granted_by`, `invited_by`; **M2M `temporary_permissions` → Permission** (empty = full temp role perms; non-empty = intersection); methods `has_active_temporary_role`, `effective_role`, `get_effective_permissions_qs()`, `has_effective_permission(codename)`. `Invitation(TenantScopedModel)`. `UserGroup(TenantScopedModel)` (mig 0009; M2M `members`; **NOT in admin**; used by `Article.allowed_groups`; "one user per group" enforced in serializer + viewset). `EmailVerificationToken`.

### Tickets (22 model classes — heaviest app)

`Pipeline`, `PipelineStage`, `TicketStatus` (incl. `pauses_sla`, `is_closed`, `is_default`), `Queue` (`default_assignee`, `auto_assign`, **`department` FK to inbox_hub.Department SET_NULL** — mig 0027), `TicketCategory`, `TicketCounter` (NOT TenantScoped; OneToOne tenant; SELECT FOR UPDATE + F-expression), `Ticket` (~64 fields; soft delete; CSAT; deal fields; `merged_into`; `auto_close_task_id`; `pre_wait_status`; `tags`+`custom_data` JSON). **`Ticket.save()` auto-populates `company_id`** from linked Contact's company when not explicitly set ([tickets/models.py:601-612](apps/tickets/models.py#L601)). `TicketLink` (4 types + circular guard via BFS), `SLAPolicy`, `EscalationRule`, `BusinessHours` (IANA timezone + schedule JSON), `PublicHoliday`, `SLAPause`, `TicketActivity` (**27 event choices**), `CannedResponse`, `Macro`, `SavedView`, `TicketAssignment` (immutable audit), `TicketWatcher` (4 reasons, `is_muted`), `TimeEntry` (1–1440 mins), `TicketTemplate`, `Webhook` (HMAC SHA-256, 8 EventType members, auto-disable at 10 failures).

> Admin registers 17 of 22 — TicketLink, TicketCounter, Macro, TicketActivity are NOT in admin.

### Contacts (5)

`Account` (CRM account; `mrr`, `health_score` clamped 0–100), `Company` (name unique per tenant), `Contact` (email unique per tenant, `email_bouncing` indexed, `lead_score` 0–100, `last_activity_at` indexed, `source` 6-choice), `ContactGroup` (M2M contacts), `ContactEvent` (append-only 360° timeline; `source` 4-choice; intentionally NOT live-broadcast). **ContactEvent NOT in admin.**

### CRM (2)

`Activity` (call/email/meeting/task), `Reminder` (formerly `Recall`; **M2M `contacts`/`tickets`** since migration 0004; priority; `status` is **derived property** of completed_at/cancelled_at/scheduled_at; `unscoped` manager; `ReminderQuerySet.overdue()/pending()/for_user()`; methods `mark_completed/mark_cancelled/reschedule`).

### Inbound Email (3)

`InboundEmail` extends `TimestampedModel` (NOT TenantScopedModel — tenant nullable, resolved post-parse). `Status` (**9 members** incl. `PARKED_IN_HUB` from migration 0009). `Direction` unified inbound+outbound; `SenderType` (customer/system/agent); `InboxStatus` (4: pending/linked/actioned/ignored); `InboxAction` (3). **NEW `assignee` FK** (SET_NULL, db_indexed, `related_name="assigned_inbound_emails"`, migration `inbound_email/0010` — untracked) + index `email_tenant_assignee_idx (tenant, assignee, inbox_status)` — set when a HubEmail is manually assigned, surfaces the original mail in the agent's Emails page (§"Agent email-inbox handoff"). Threading: `message_id` (indexed, stored without `<>`), `in_reply_to`, `references`. Idempotency keys: `"in:{tenant_id}:{message_id}"` / `"out:{tenant_id}:{ticket_id}:{message_id}"`. `is_read` indexed (migration 0007). `save()` enforces immutability of `linked_at/by` + `actioned_at/by` once set (**`assignee` is mutable**). `BounceLog` for hard bounces. `IMAPPollState` (`uid_validity`+`last_uid` watermark). **Only InboundEmail in admin** — BounceLog and IMAPPollState are not (admin does not yet expose `assignee`).

### Knowledge (6)

`Category`, `Article` (status: draft/pending_review/published/rejected/flagged; visibility: internal/public; review workflow + Postgres FTS via `SearchVectorField` + GinIndex; PDF/DOCX via mammoth + sanitisation; **`allowed_groups` M2M to UserGroup** — migration 0005; auto-slug with collision suffix via `Article.unscoped` scan). **`Article.save()` resolves tenant** from `main.context.get_current_tenant()` before slug scan, falls back to `"article"` when `slugify(title)` empty. `KBRevision`, `KBVote` (session_key-keyed), `KBSearchGap`, `KBTicketLink`. **Only Category + Article in admin.**

### Kanban (3)

`Board` (`resource_type` TICKET/DEAL, `is_default`, `is_personal` migration 0004 — private to creator), `Column` (board, order, optional status FK, wip_limit, color), `CardPosition` (polymorphic GenericFK; unique on column+content_type+object_id).

### Comments / Messaging / Newsfeed / Notifications / Inbox Hub

**comments** (4): `Comment` (polymorphic GenericFK, threaded, `is_internal`), `Mention`, `CommentRead`, `ActivityLog` (**34 action choices** after migration 0010 — adds 8 `EMAIL_*` for Inbox Hub, all now emitted).

**messaging** (3): `Conversation` (DIRECT/GROUP/TICKET; FK `source_group` to UserGroup migration 0002), `ConversationParticipant`, `Message` (threaded; mentions M2M; `is_edited`).

**newsfeed** (3): `NewsPost` (5 categories), `NewsPostReaction` (6 emoji), `NewsPostRead` (NOT tenant-scoped — row existence = read).

**notifications** (2): `Notification` (**20 NotificationType choices** after migration 0005 — adds 5 `HUB_EMAIL_*`, all now created by Phase 1B). **`Notification` is NOT polymorphic** — only a `data` JSONField. `NotificationPreference`.

**inbox_hub** (8): see §Inbox Hub above.

### Agents / Custom Fields / Billing / Analytics / Attachments / Notes / API Keys / VoIP

**agents** (2): `AgentAvailability` (online/away/busy/offline + `custom_status` FK; load fields + working hours JSON + `auto_away_outside_hours`; **+`last_seen` (mig 0007) + `presence_fresh`/`is_assignable`/`remaining_capacity` properties** for Inbox Hub presence-aware assignment); `CustomAgentStatus` (migration 0006; tenant-scoped; `StatusColor` 8-choice). `BUILTIN_STATUS_SLUGS` frozenset. **CustomAgentStatus NOT in admin.**

**custom_fields** (2): `CustomFieldDefinition` (8 field types × 3 modules; M2M `visible_to_roles`), `CustomFieldValue` (EAV; 4 typed value columns).

**billing** (4): `Plan` (tiered + `has_voip`, `has_call_recording`, `max_calls_per_month`, `audit_retention_days` from migration 0002), `Subscription` (1:1 Tenant; 6 status choices; 7-day `in_grace_period`), `Invoice`, `UsageTracker`.

**analytics** (4): `ReportDefinition`, `DashboardWidget`, `ExportJob` (CSV/XLSX/PDF), `CalendarEvent` (`color`/`end_date` migration 0003). **CalendarEvent NOT in admin.**

**attachments** (1): `Attachment` (polymorphic GenericFK; `tenants/{id}/attachments/YYYY/MM/{filename}`).

**notes** (1): `QuickNote` (6 colors; pinning; per-user).

**api_keys** (1): `APIKey(TenantScopedModel)` — `name`, `service_user` (1:1 hidden `User` with `is_service_account=True`), `role` (FK PROTECT — drives `HasTenantPermission`), `prefix` (indexed), `hashed_key` (SHA-512 hex; cleartext never persisted), `created_by` PROTECT, `is_active`, `expires_at`, `last_used_*`, `request_count`. Cleartext format: `kz_live_<slug6>_<token_urlsafe(32)>`. Sister files: `authentication.py`, `services.py`, `throttling.py`, `middleware.py` (`RateLimitHeadersMiddleware`), `extensions.py` (drf-spectacular `APIKeyAuthScheme`, registered via `apps.py::ready()`), `views.py`, `tasks.py`.

**voip** (5): `VoIPSettings` (singleton; encrypted ARI creds; STUN/TURN; `pjsip_context`), `Extension` (sip_username **globally unique**, encrypted password), `CallLog` (direction 3-choice, status 9-choice, indexed `asterisk_channel_id`), `CallRecording` (1:1 CallLog; `tenants/{id}/recordings/YYYY/MM/{uuid}.{ext}`), `CallQueue` (5 ACD strategies + M2M Extension members).

### Polymorphic (GenericFK) Models — 5 total

`Attachment`, `Comment`, `ActivityLog`, `CustomFieldValue`, `CardPosition`. **Not** Notification (data JSONField only).

## Role-Based Access Control

**Hierarchy:** Admin(10) → Manager(20) → **Team Lead(25)** → Agent(30) / **IT(30)** / **HR(30)** → Viewer(40).

**Default role seeding (`apps/tenants/signals.py::create_default_roles`)** runs on `Tenant.post_save (created=True)` and seeds **all seven** system roles inline. All `is_system=True`. Permission sets for the **six** perm-bearing roles (Viewer is intentionally permission-less, leans on ≤40 view fallback) come from `apps/accounts/defaults.py::ROLE_DEFINITIONS` (6 entries). Backfill for existing tenants via `accounts/0011_seed_team_lead_it_hr_roles`. The 12 Inbox Hub permissions are backfilled via `accounts/0012_seed_inbox_hub_permissions` (`.add(*perms)`). `PERMISSION_DEFINITIONS` → `ALL_CODENAMES` = **69 unique codenames** (12 inbox_hub-related).

- `is_admin`: `hierarchy_level ≤ 10`; `is_admin_or_manager`: `≤ 20`; `is_agent_or_above`: `≤ 30`. **Team Lead (25)** satisfies `is_agent_or_above` but NOT `is_admin_or_manager`. **IT/HR (30)** satisfy `is_agent_or_above`. Viewer (40) satisfies none.
- Non-manager row-scoping (`level > 20`): the membership sees only own/assigned tickets, linked contacts, filtered kanban cards, own reminders/activities.
- **Always use `TenantMembership.effective_role`** — temporary role wins until `temporary_role_expires_at`. `BadgeCountView` and the inbox_hub permission classes use `effective_role`. ⚠️ **Mixed `role` vs `effective_role` (pre-existing footgun, now more visible):** the ticket-list queryset (`tickets/views.py`), `analytics/services.py::_apply_user_filter`, and `kanban/serializers.py` still gate the agent branch on raw `membership.role.hierarchy_level`, whereas `IsTicketAccessible` and the badges use `effective_role`. A temp-promoted agent gets object-perm + badges as a manager but is still list/kanban/analytics-filtered.
- **`AgentAvailabilityViewSet.assignable_roles`** **excludes the `admin` slug** to prevent privilege escalation through the UI. Each returned role dict includes a `description` field.
- **Shared visibility modules (NEW Jun-9 refactor)** — two single-source-of-truth helper modules so a scoping rule can't drift between surfaces:
  - **`apps/tickets/access.py`** — `agent_visible_tickets_q(user)` = `Q(assignee=user) | (Q(created_by=user) & Q(assignee__isnull=True))` + object-level `agent_can_see_ticket(user, ticket)`. **An agent sees a ticket only if it's assigned to them, or they created it AND it's still unassigned — a self-created ticket handed off to another agent LEAVES the creator's view.** Imported by `tickets/views.py` (list), `accounts/permissions.py::IsTicketAccessible` (object), `analytics/services.py` (dashboards), `nav/views.py` (badge), `kanban/serializers.py` (cards). Admin/Manager (≤20) bypass.
  - **`apps/inbox_hub/access.py`** — `can_access_inbox_hub(membership)` (Admin ≤10 OR `user_in_any_group`) + `user_in_any_group(user, tenant)`. The Hub group gate; see §Inbox Hub. Imported by `inbox_hub/permissions.py`, `inbox_hub/views.py`, `tenants/frontend_views.py`, `tenants/context_processors.py`, `nav/views.py`.
- **Permission classes** (`apps/accounts/permissions.py`):
  - `HasTenantPermission` — codename-based; `ACTION_MAP` maps 70+ DRF action names to `{resource}.{action}`; includes `convert_to_ticket → convert`, `dismiss → dismiss`, `send_creation_email → update`. Note: `assign`/`escalate` are deliberately NOT remapped here (they collide with TicketViewSet) — Inbox Hub uses its own `HubEmailPermission`. Hierarchy fallback: `view → ≤40`, `create/update → ≤30`, all others → ≤20.
  - `IsTicketAccessible` — object-level row filtering for agents (≤20 bypass; otherwise delegates to **`agent_can_see_ticket(user, obj)`** — the tightened shared rule above).
  - `IsTenantMember`, `IsTenantAdmin`, `IsTenantAdminOrManager`.
  - **`HubEmailPermission` + `IsHubEmailAccessible`** (`apps/inbox_hub/permissions.py`) — Inbox Hub's local stack with the group access gate; see §Inbox Hub.
  - Helper `_get_membership()` caches on `request._cached_tenant_membership` (`select_related("role","temporary_role")`).
- `_role_required(20)` decorator gates admin/manager frontend pages. **Team Lead (25) does NOT pass `_role_required(20)`** by design. `_role_required(30)` gates Outbound Emails (admits Team Lead/Agent/IT/HR). **`/settings/`** is `@_membership_required + @ensure_csrf_cookie` — any member can load; API enforces admin-only writes (with per-field allowlist for Managers).

## Signals (10 apps with signals.py + notifications/signal_handlers.py)

**42 total receivers** wired up. Apps with `signals.py`: accounts, comments, contacts, crm, custom_fields, knowledge, newsfeed, tenants, tickets, voip. `notifications` uses `signal_handlers.py`. **`apps/inbox_hub/` has NO `signals.py`** — `apps.py` has no `ready()`; the Hub fans events imperatively from its service/routing/assignment layer.

### Tenants — 2 receivers
- `Tenant.post_save (created=True)` → `create_tenant_settings` + `create_default_roles` (seeds 7 system roles inline).

### Accounts — 5 receivers
- `TenantMembership.post_save` → `create_profile_on_membership` + `broadcast_membership_save`
- `TenantMembership.post_delete` → `broadcast_membership_delete`
- `Profile.post_save` → `broadcast_profile_save`
- `User.post_save` → `broadcast_user_save` (skips creation; on update fans across every active membership's tenant group; emits `avatar` URL in payload via `_serialise_user`)

### Tickets — 11 receivers
- `Ticket.pre_save` → `handle_ticket_status_change`
- `Ticket.post_save` → `fire_ticket_created_signal`, `fire_ticket_assigned_signal`, `log_ticket_activity` (2s dedup + respects `_skip_signal_logging`), `handle_sla_pause_on_status_change`, `create_kanban_card_on_ticket_save`, `sync_kanban_card_on_status_change`, `sync_kanban_card_on_pipeline_stage_change`
- `Ticket.post_delete` → `remove_kanban_cards_on_ticket_delete` (commit `79eeb88`) — hard-delete cleanup via `CardPosition.unscoped`
- `@receiver(ticket_closed)` → `check_kb_article_coverage`
- `SLAPolicy.post_save` → `propagate_sla_policy_change` (async via Celery if >50 tickets)

### Custom Fields — 2 receivers
- `Ticket.post_save`/`Contact.post_save` → sync `CustomFieldValue` from JSON `custom_data`

### Knowledge — 1 receiver
- `Article.post_save` → `update_search_vector` (Postgres FTS; uses `.update()` to avoid recursion; skips non-Postgres)

### Notifications — 2 receivers (in `signal_handlers.py`)
- `@receiver(ticket_assigned)` → `handle_ticket_assigned`
- `@receiver(ticket_comment_created)` → `handle_comment_notification` + `_queue_contact_reply_email`

### VoIP — 1 receiver
- `CallLog.post_save` on terminal status → writes `TicketActivity` + `ActivityLog` + queues `process_call_recording`. `_timeline_logged` flag dedup; `_TERMINAL_STATUSES` frozenset.

### Comments — 2 receivers
- `Comment.post_save/delete` → `broadcast_comment_save/delete`

### Contacts — 8 receivers
- `Contact/Company/Account/ContactGroup × post_save/delete` → `broadcast_*_save/delete`

### CRM — 4 receivers
- `Activity/Reminder × post_save/delete` → `broadcast_*_save/delete` (Reminder verb resolved by state)

### Newsfeed — 4 receivers
- `NewsPost/NewsPostReaction × post_save/delete` → `broadcast_newspost_*` + `broadcast_reaction_*` (with `added: bool`)

## Dual-Write Logging

**Two parallel log systems:**
1. **TicketActivity** — human-readable timeline, **27 events** (after migration 0026). Endpoint: `/api/v1/tickets/tickets/{id}/timeline/`.
2. **ActivityLog** — polymorphic audit trail with diffs+IP, **34 actions** (after migration 0010 — 26 pre-Inbox-Hub + 8 `EMAIL_*`). Endpoint: `/api/v1/tickets/tickets/{id}/activity/`.

**Dedup pattern:** The signal `log_ticket_activity` checks `instance._skip_signal_logging`. **Service-layer functions** (`assign_ticket`, `change_ticket_status`, `escalate_ticket`, `change_ticket_priority`) set this flag before their `ticket.save(update_fields=…)`. ViewSets also set it; use `serializer.instance` in `perform_update` so the flag persists. 2-sec window in signal.

**Service layer** (`apps/tickets/services.py`) — every mutation writes to BOTH logs atomically and broadcasts WebSocket events via `transaction.on_commit()`. Public: `create_ticket_activity`, `assign_ticket`, `transition_ticket_status`, `change_ticket_status`, `change_ticket_priority`, `log_ticket_comment`, `close_ticket`, `escalate_ticket`, `merge_tickets`, `split_ticket`, `bulk_update_tickets`, `apply_macro/render_macro`, `record_first_response`, `transition_pipeline_stage`, `initialize_sla`, `log_sla_change`, `broadcast_ticket_event`, `validate_status_transition`, `resume_from_wait`. **`ALLOWED_TRANSITIONS["waiting"] = ["open","in-progress","resolved","closed"]`** — Waiting→Resolved/Closed legal.

**Kanban drags route through services.** `apps/kanban/services.py::move_card(...)` — when dragged card is a `Ticket` AND target column has a different status, calls `apps.tickets.services.change_ticket_status(content_obj, target_column.status, actor, request=request)`. Full dual-write + ticket-feed broadcast + SLA pause handling.

**Kanban orphan-card cleanup** (`79eeb88`): `apps/tickets/signals.py` registers a `post_delete` receiver that hard-deletes `CardPosition` rows via `CardPosition.unscoped`. **Companion**: `apps/kanban/serializers.py` skips cards whose resolved content object is `None`.

**Inbox Hub dual-write parity**: `park_email_in_hub`, routing, assignment, transition, escalate, reassign, note, `convert_to_ticket`, `dismiss_hub_email` each write an `ActivityLog` row (one of the 8 `EMAIL_*` actions or `STATUS_CHANGED`) AND broadcast a `hub_email.*` LiveBus event on commit. No `TicketActivity` writes — HubEmail's own audit surface is `HubEmailAssignment` (immutable) + `HubEmailNote`.

**Webhook service** (`apps/tickets/webhook_service.py`): `deliver_webhook` HMAC SHA-256, 10s timeout, auto-disable at 10 failures. `fire_webhooks(tenant, event_type, data)` async via Celery. Events: 8 EventType members.

**Transaction safety:** Notifications + WebSocket pushes + email task queues all defer to `transaction.on_commit()`.

## SLA + Business Hours (`apps/tickets/sla.py`)

Single breach-detection entry point `get_effective_elapsed_minutes()`:
- Resolves per-tenant schedule via `BusinessHours` (JSON per-day + IANA timezone) or legacy `TenantSettings` flat fields.
- Skips `PublicHoliday` dates.
- Subtracts total pause duration from `SLAPause` records.
- Helpers: `elapsed_business_minutes()`, `add_business_minutes()`, `is_within_business_hours()`, `get_total_pause_minutes()`, `sla_deadline_utc()`.
- `initialize_sla(ticket)` seeds `response_deadline`/`resolution_deadline`.
- `_check_first_response_breach` uses atomic UPDATE+WHERE.

> **Inbox Hub SLA is separate and simpler** — `_initialize_hub_sla` seeds **wall-clock** deadlines from `HubEmailSLA`; `check_hub_sla_breaches` (Beat 120s) flags + warns + auto-escalates. No business-hours math yet.

## Inbound / Outbound Email

### Inbound (`apps/inbound_email/`)
- **In-process SMTP server** via `aiosmtpd`, launched by `run_smtp_server` (PM2 process `kanzan-smtp`). Optional STARTTLS + LOGIN/PLAIN AUTH.
- **IMAP poller** — shared Gmail-style mailbox; UID > watermark (not UNSEEN). Driven by `fetch_inbound_emails_task` (Celery Beat 60s). Disabled when `IMAP_HOST` blank. **Safety: never backfills** — aborts on UIDVALIDITY/UIDNEXT parse failure.
- **Tenant resolution** — 4 strategies in `resolve_tenant_from_address`:
  1. Plus-addressing (`support+{slug}@…`)
  2. Subdomain routing
  3. Custom `TenantSettings.inbound_email_address`
  4. `settings.IMAP_DEFAULT_TENANT_SLUG` last-resort fallback — shared mailboxes route to a default tenant
- **Filters** run BEFORE tenant resolution: loop detection, noreply senders, RFC 3834 Auto-Submitted / Precedence: bulk/junk/list. `classify_email() → bounce/auto_reply/loop/legitimate`. Bounces write `BounceLog` and flip `Contact.email_bouncing=True`.
- **Threading** — `find_existing_ticket` 3-tier: In-Reply-To → References (reversed) → subject `[#N]` regex.
- **Processing pipeline** (`process_inbound_email_task`, max_retries=3, default_retry_delay=30s, acks_late): `select_for_update` → filter classifier → tenant resolution → idempotency claim → find/create contact → find existing ticket OR (per the seam) `park_email_in_hub` if `inbox_hub_enabled` else `_create_ticket_from_email` (with `_maybe_auto_assign`) → attach files → queue confirmation email via `transaction.on_commit()`.
- **Agent inbox workflow** (`inbox_services.py`): `link_email_to_ticket`, `action_email`, `ignore_email`. The `InboundEmailViewSet` (read via `/inbound-email/` + alias `/emails/`) gained **`?assigned=me` / `?internal=true` / `?mine=true`** query branches and a **`create_ticket`** write action (`POST /{id}/create-ticket/`, role ≤30; reuses `convert_to_ticket` for parked Hub mail) — backing the Emails page's "Assigned to me" tab (§"Agent email-inbox handoff").

### Outbound (`apps/tickets/email_service.py`)
- `send_ticket_email()` — single entry point. RFC-compliant Message-IDs.
- Persists an OUTBOUND `InboundEmail` record for threading.
- Dev: `filebased.EmailBackend` → `tmp/emails/`. Prod: SMTP.

## Auto-Assign (Inbound Email → Agent)

`apps/agents/services.py::pick_email_agent(tenant)` (legacy ticket auto-assign, distinct from the Inbox Hub AssignmentEngine):
1. Active tenant member with `hierarchy_level == 30` (Agent / IT / HR — all eligible).
2. Not OFFLINE; agents with no `AgentAvailability` eligible.
3. Pick fewest open tickets (load balancing).
4. Tie-break by **least-recently-assigned** (`MAX(TicketAssignment.created_at)`, NULLS FIRST for cold-start fairness).

`auto_assign_email_ticket(ticket)` — atomic save + `TicketAssignment` audit + best-effort `AgentAvailability.current_ticket_count` nudge.

## VoIP

**Architecture:** Asterisk/FreePBX → ARI (REST + WebSocket Stasis). Django wraps ARI, exposes SIP creds to browser softphone (SIP.js over WSS), persists `CallLog`/`CallRecording`.

- **`ari_client.py`** — async `httpx` ARIClient: originate/hangup/hold/unhold/mute/unmute/redirect/bridge/record. `ARIEventListener` connects to `ws(s)://host:port/ari/events?app=kanzan-voip&subscribeAll=true`, exponential reconnect 1–30s.
- **`services.py`** — sync wrappers; `process_ari_event` dispatches state events to `CallLog` updates.
- **`consumers.py`** — `CallEventConsumer` (`ws/voip/events/`).
- **`run_ari_listener`** — long-running async listener; one per active tenant concurrently. **NOT in PM2** by default.
- **Softphone** — `templates/includes/softphone.html` + `static/js/voip-softphone.js` using **SIP.js 0.21.2** (CDN, conditional on `voip_enabled`).

## API Architecture

### Authentication
- **API:** JWT (SimpleJWT) — 15min access, 7-day refresh, rotate + blacklist, HS256. **`APIKeyAuthentication`** (`Authorization: Api-Key kz_live_<slug6>_<secret>`). Returns `None` (not 401) when header absent/different scheme. Fails closed (401) on valid-format but invalid/revoked/expired/cross-tenant key.
- **`DEFAULT_AUTHENTICATION_CLASSES` order:** `JWTAuthentication` → `APIKeyAuthentication` → `SessionAuthentication`. JWT tried FIRST.
- **Frontend:** Session auth (Redis cached_db, host-only cookie).
- **SSO:** django-allauth (Google, Microsoft, OIDC) — `ACCOUNT_LOGIN_METHODS = {"email"}` (a set). `django.contrib.sites` NOT in INSTALLED_APPS; allauth ≥65 runs without it.
- **Global logout:** `User.auth_version` bumped invalidates all prior sessions via `SessionVersionMiddleware`.

### `/api/v1/` Endpoint Map (23 router includes / 22 unique URLConfs — `inbound-email/` dual-mounts as `emails/`)

```
/tenants/            TenantViewSet (slug lookup), TenantSettingsViewSet (singleton; per-field Manager allowlist incl. inbox_hub_* toggles)
/accounts/           AuthViewSet (throttle_scope="auth"), UserViewSet, RoleViewSet, ProfileViewSet, InvitationViewSet, TenantMembershipViewSet, UserGroupViewSet
/api-keys/           APIKeyViewSet (admin-only; mint/list/reveal-once/regenerate/revoke)
/tickets/            TicketViewSet (~36 custom actions), TicketStatusViewSet, QueueViewSet, TicketCategoryViewSet, SLAPolicyViewSet, EscalationRuleViewSet, CannedResponseViewSet, MacroViewSet, SavedViewViewSet, BusinessHoursViewSet (singleton), PublicHolidayViewSet, TicketTemplateViewSet, WebhookViewSet, CSATSubmitView (public)
/contacts/           ContactViewSet, CompanyViewSet, AccountViewSet, ContactGroupViewSet
/billing/            PlanViewSet (AllowAny), SubscriptionViewSet (+cancel/reactivate), InvoiceViewSet, UsageViewSet, checkout, webhook (CSRF-exempt, Stripe-signed)
/kanban/             BoardViewSet (+detail), ColumnViewSet, CardPositionViewSet (+move/reorder/add-ticket; actor+request aware)
/comments/           CommentViewSet, ActivityLogViewSet (read-only)
/messaging/          ConversationViewSet (+add/remove/leave/search-participants), MessageViewSet (+broadcast author-only action)
/notifications/      NotificationViewSet (+mark_read, unread_count, admin cleanup), NotificationPreferenceViewSet
/attachments/        AttachmentViewSet (multipart upload, cross-tenant validated)
/analytics/          DashboardView (APIView), ReportDefinitionViewSet, DashboardWidgetViewSet, ExportJobViewSet, CalendarEventViewSet
/agents/             AgentAvailabilityViewSet (10+ actions incl. grant_temp_role/revoke_temp_role/reactivate; assignable_roles excludes admin slug), CustomAgentStatusViewSet
/custom-fields/      CustomFieldDefinitionViewSet, CustomFieldValueViewSet (read-only)
/knowledge/          CategoryViewSet, ArticleViewSet (+submit_for_review/approve/reject/record_view/remove_file/preview_file/vote), KBSearchView
/notes/              QuickNoteViewSet
/inbound-email/      InboundEmailViewSet (read + create-ticket action; ?assigned=me/?internal=true/?mine=true filters) + InboxViewSet (link/action/ignore)
/emails/             alias mount of inbound_email.api_urls (namespace="emails_api")
/crm/                ActivityViewSet (+my-tasks), ReminderViewSet (+overdue/stats/complete/cancel/reschedule/bulk-action), PipelineForecastView
/nav/                BadgeCountView (7 categories incl. inbox_hub; effective_role; capped at 99 per category)
/newsfeed/           NewsPostViewSet (+react/mark-read/mark-all-read/unread-count)
/voip/               VoIPSettingsViewSet, ExtensionViewSet, CallLogViewSet (+active/stats), InitiateCallView, CallHoldView, CallTransferView, CallHangupView, SIPCredentialsView, CallRecordingDownloadView, CallQueueViewSet
/inbox-hub/          HubEmailViewSet (list/retrieve + convert-to-ticket/dismiss/assign/reassign/claim/escalate/transition/note) + DepartmentViewSet + RoutingRuleViewSet + HubEmailSLAViewSet + QueueRoutingViewSet
```

**Non-HTTP inbound channel:** `kanzan-smtp` PM2 process at `SMTP_SERVER_HOST:SMTP_SERVER_PORT` (default `0.0.0.0:2525`) feeds the same `InboundEmail` + Celery pipeline.

**Docs:** `/api/docs/` (Swagger UI — shows both `ApiKeyAuth` and JWT Bearer), `/api/schema/` (OpenAPI 3.0 JSON).

### Public / unauthenticated endpoints
- `POST /api/v1/tickets/csat/` — `CSATSubmitView` (signed token validates caller)
- `GET /api/v1/billing/plans/` — `PlanViewSet` `[AllowAny]`
- `POST /api/v1/billing/webhook/` — `stripe_webhook` (HMAC validated; `@csrf_exempt`)
- `AuthViewSet.register/login/accept_invitation` — `[AllowAny]`, `throttle_scope="auth"`

### Frontend Routes (`apps/tenants/frontend_urls.py`) — **35 paths**

```
/                             landing_page
/login/                       login_page
/register/                    register_page
/logout/                      logout_page
/auth/handoff/                auth_handoff
/verify-email/                verify_email_page
/verify-email-sent/           verify_email_sent_page
/setup-company/               setup_company_page    @login_required
/workspaces/                  workspaces_page       @login_required
/dashboard/                   dashboard_page        @_membership_required
/tickets/                     ticket_list_page      @_membership_required
/tickets/new/                 ticket_create_page    @_membership_required
/tickets/<ticket_number>/     ticket_detail_page    @_membership_required
/contacts/                    contact_list_page     @_membership_required
/contacts/create/             contact_create_page   @_membership_required
/contacts/<contact_id>/       contact_detail_page   @_membership_required
/calendar/                    calendar_page         @_membership_required
/kanban/                      kanban_page           @_membership_required
/messaging/                   messaging_page        @_membership_required
/analytics/                   analytics_page        @_membership_required
/users/                       users_page            @_role_required(20)
/settings/                    settings_page         @_membership_required + @ensure_csrf_cookie  (API enforces admin write)
/billing/                     billing_page          @_role_required(20)
/agents/                      agents_page           @_role_required(20)
/groups/                      groups_page           @_role_required(20)
/emails/                      emails_page           @_role_required(30)        ← Agent-level (outbound log)
/knowledge/                   knowledge_list_page   @_membership_required
/knowledge/<article_slug>/    knowledge_article_page @_membership_required
/profile/                     profile_page          @_membership_required
/api/quickstart/              api_quickstart_page   @_membership_required
/inbound-email/               inbound_email_page    @_membership_required (agent inbox)
/inbox-hub/                   inbox_hub_page        @_inbox_hub_access_required  (login + active membership + group-gate → 403.html)
/reminders/                   reminders_page        @_membership_required
/audit-log/                   audit_log_page        @_role_required(20)
/calls/                       calls_page            @_membership_required
```

## WebSocket Endpoints (6 total — `main/asgi.py`)

Stack: `ProtocolTypeRouter({"http": django_asgi_app, "websocket": AllowedHostsOriginValidator(AuthMiddlewareStack(WebSocketTenantMiddleware(URLRouter(messaging_ws + notification_ws + ticket_ws + voip_ws + live_ws))))})`.

`WebSocketTenantMiddleware` decodes Host from scope, resolves Tenant, sets `scope["tenant"]` and binds `set_current_tenant()` for connection lifetime; clears in `finally`.

1. **Chat:** `ws/messaging/{conversation_id}/` → `ChatConsumer`. Actions: `send_message`, `typing`, `mark_read`. Group: `chat_{conversation_id}`. Limits: 10KB/msg, 5 msg/s, 2s typing cooldown.
2. **Notifications:** `ws/notifications/` → `NotificationConsumer`. Group: `notifications_{user_id}`. Inbound: `{action: "mark_read", notification_id}`.
3. **Ticket Presence:** `ws/tickets/{ticket_id}/presence/` → `TicketPresenceConsumer`. Group: `ticket_{ticket_id}_presence`. **Known gap:** docstring mentions `presence_list` for newcomers — **not implemented**.
4. **Ticket Feed:** `ws/tickets/feed/` → `TicketListConsumer`. Group: `ticket_feed_{tenant_id}`. Read-only.
5. **VoIP:** `ws/voip/events/` → `CallEventConsumer`. Group: `voip_{tenant_id}`.
6. **Live:** `ws/live/` → `LiveEventConsumer`. Group: `live_tenant_{tenant_id}`. Anon → close 4001; non-member → close 4003. **Stamps agent presence on connect + each `ping` heartbeat** (Phase 1B).

> `apps/inbox_hub/routing.py` is the **RoutingEngine** (email→department classification), NOT a Channels route — it is not in `asgi.py`.

## Celery Tasks & Beat Schedule

### Queue Routing (`main/celery.py` — 7 globs + default)
```
apps.billing.tasks.*                              → kanzan_webhooks   (dormant — apps/billing/tasks.py does not exist)
apps.notifications.tasks.send_email_*             → kanzan_email
apps.notifications.tasks.send_notification_email  → kanzan_email
apps.inbound_email.tasks.*                        → kanzan_email
apps.tickets.tasks.send_ticket_*                  → kanzan_email
apps.api_keys.tasks.send_api_key_*                → kanzan_email
apps.voip.tasks.*                                 → kanzan_voip
*                                                 → kanzan_default
```
(No route for `apps.inbox_hub.tasks.*` or `apps.agents.tasks.*` — both fall through `*` to `kanzan_default`.) **`CELERY_BEAT_SCHEDULE` lives in `main/settings/base.py`, NOT in `celery.py`.**

### Beat Schedule (11 tasks)

| Beat key | Task name | Schedule |
|----------|-----------|----------|
| `check-sla-breaches` | `apps.tickets.tasks.check_sla_breaches` | 120s |
| `cleanup-old-notifications` | `apps.notifications.tasks.cleanup_old_notifications` | 86400s (daily) |
| `check-overdue-tickets` | `apps.tickets.tasks.check_overdue_tickets` | 900s (15m) |
| `calculate-lead-scores` | `apps.crm.tasks.calculate_lead_scores` | 86400s (daily) |
| `calculate-account-health-scores` | `apps.crm.tasks.calculate_account_health_scores` | 86400s (daily) |
| `kb-stale-alert` | `knowledge_base.alert_stale_articles` | crontab daily 08:00 |
| `kb-gap-digest` | `knowledge_base.send_gap_digest` | crontab Monday 09:00 |
| `cleanup-stale-calls` | `apps.voip.tasks.cleanup_stale_calls` | 3600s (hourly) |
| `fetch-inbound-emails` | `apps.inbound_email.tasks.fetch_inbound_emails_task` | 60s |
| **`reap-stale-presence`** | `apps.agents.tasks.reap_stale_presence` | **60s** (NEW — ages out dead /ws/live/ sessions, ONLINE→AWAY) |
| **`check-hub-sla-breaches`** | `apps.inbox_hub.tasks.check_hub_sla_breaches` | **120s** (NEW — Inbox Hub SLA breach + escalation sweep) |

Celery Beat uses the **built-in shelve scheduler** (`celerybeat-schedule`). `django-celery-beat` removed (Django 6 incompat). `apps.crm.tasks.check_overdue_reminders` and `apps.tickets.tasks.check_sla_breach_warnings` exist but are **NOT in Beat**.

### Task Inventory (26 tasks across 10 modules)

- **agents** (NEW): `reap_stale_presence` (queue `kanzan_default`; cross-tenant `.unscoped`; chunk_size=200)
- **inbox_hub** (NEW): `check_hub_sla_breaches` (queue `kanzan_default`; cross-tenant; warns + auto-escalates)
- **notifications**: `send_notification_email` (retries=3, default_retry_delay=60s, acks_late, kanzan_email), `cleanup_old_notifications`
- **analytics**: `process_export_job` (retries=3; CSV/XLSX; openpyxl optional → CSV fallback)
- **inbound_email**: `fetch_inbound_emails_task`, `process_inbound_email_task` (retries=3, default_retry_delay=30s, acks_late)
- **tickets**: `check_sla_breaches`, `check_overdue_tickets`, `send_ticket_reply_email_task`, `send_ticket_created_email_task`, `send_ticket_email_task`, `auto_close_ticket` (two-guard idempotency), `send_csat_survey_email`, `deliver_webhook_task` (exp backoff), `check_sla_breach_warnings`, `propagate_sla_policy_change_task`
- **voip**: `process_call_recording`, `cleanup_stale_calls`, `sync_call_state` (queue `kanzan_voip`)
- **crm**: `check_overdue_reminders` (NOT in Beat), `calculate_lead_scores`, `calculate_account_health_scores`
- **knowledge**: `alert_stale_articles`, `send_gap_digest` (registered as `knowledge_base.*`)
- **api_keys**: `send_api_key_created_email_task` (bind, retries=3, default_retry_delay=60s, acks_late)

## PM2 Processes — 5 prod / 4 dev

### `ecosystem.config.js` (prod, venv at `.venv/`)

| Name | Script | Purpose | Max mem |
|------|--------|---------|--------|
| `kanzan-django` | `.venv/bin/gunicorn main.asgi:application -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8001 --timeout 120 --graceful-timeout 30` | HTTP + WebSocket | 2GB |
| `kanzan-celery-worker` | `.venv/bin/celery -A main worker -Q kanzan_default,kanzan_email,kanzan_webhooks -c 4 --pool prefork --max-tasks-per-child=200 -n kanzan-worker@%h` | Background jobs | 2GB |
| `kanzan-celery-beat` | `.venv/bin/celery -A main beat -l info` | Periodic scheduler | 512MB |
| `kanzan-flower` | `.venv/bin/celery -A main flower --port=5556 --url_prefix=flower --basic_auth=$KANZAN_FLOWER_AUTH` | Monitoring | 512MB |
| `kanzan-smtp` | `.venv/bin/python manage.py run_smtp_server` | In-process SMTP (2525) | 512MB |

Common: `kill_timeout=8000ms` (15000ms worker), `max_restarts=10`, `min_uptime="10s"`, `watch=false`.

> **Prod worker `-Q` list is `kanzan_default,kanzan_email,kanzan_webhooks`.** New Inbox Hub/presence tasks route to `kanzan_default` (covered). `kanzan_voip` is in `main/celery.py` routes but the default worker doesn't subscribe; add it or start a dedicated VoIP worker. `run_ari_listener` is **not in PM2** by default.
> **Makefile `stop`/`restart` omit `kanzan-smtp`** — manage independently with `pm2 stop kanzan-smtp`.

### `ecosystem.dev.config.js` (dev, venv at `env/` symlinked to `.venv/`) — 4 processes
- `kanzan-django` runs `manage.py runserver 0.0.0.0:8001` (auto-reload).
- `kanzan-celery-worker` `-c 2 --max-tasks-per-child=50`, **watch enabled** on `apps/*/tasks.py`, `apps/*/services.py`, `main/celery.py` with 2s delay, 1GB.
- `kanzan-celery-beat`, `kanzan-flower` (same as prod, lower memory).
- **No `kanzan-smtp` in dev.** Common: `max_restarts=50`, `min_uptime="3s"`.

## Frontend Architecture

### JavaScript (`static/js/`, 14 modules, 5,262 LOC — vanilla, no React/Vue)

| Module | LOC | Role |
|--------|----:|------|
| `app.js` | 880 | Global init: alerts, sidebar collapse, density, notification WS, Toast, `Kanzan.formatDate/…`, sidebar badge polling (7 categories incl. emails + inbox_hub), `initLiveStatusPill()`, `initSidebarUserLive()` (avatar bg-image), `initSidebarBadges()` (LiveBus subs for hub_email.* with 500ms debounce). Bell/flyout animations (3s auto-fade) |
| `inbox-hub.js` | **1,177** | Vanilla IIFE controller for `/inbox-hub/` — **triage COCKPIT** (cache-bust **`?v=8`**). 5 workload lenses (all/unassigned/mine/oldest/sla, SLA self-hides at 0); 4-count HEAD fetch; per-email customer-context card via `GET /hub-emails/{id}/context/` (contextCache + contextReqId); SLA badge + open-ticket nudge; convert/assign/dismiss + floating body-portaled Assign menu; keybinds J/K/C/A/X/Esc; 7 LiveBus subs (400ms, list+counts only); DOMPurify body rendering. NO claim/escalate/transition/note UI. |
| `voip-softphone.js` | 710 | SIP.js 0.21.2 + `CallEventConsumer`. Dial pad, DTMF, mute/hold/transfer/hangup, incoming-call modal |
| `custom-select.js` | 371 | `KanzenSelect` global with portal rendering + searchable when >8 options |
| `command-palette.js` | 337 | Cmd+K modal |
| `keyboard-shortcuts.js` | 318 | Global hotkeys (j/k/Enter/Esc/a/s/x/c/?, g d/t/c/b go-to). Injects runtime `<style>` using `var(--crm-primary)` |
| `ticket-feed.js` | 248 | WebSocket `ws/tickets/feed/`. Auto-connects via `data-ticket-feed` or URL match. Banner + row pulse. Publishes into LiveBus |
| `agent-availability.js` | 244 | Status toggle + persistence; **`subscribePresence()` reflects server `agent.presence` in navbar pill** (skips when a custom status is chosen) |
| `notes-panel.js` | 238 | Quick notes CRUD (6 colors, pinning, localStorage) |
| `live-connection.js` | 206 | Single shared `wss?://host/ws/live/`, **25s heartbeat ping (drives presence)** / 8s pong, infinite backoff |
| `rich-editor.js` | 191 | TipTap wrapper. Page-specific. TipTap via importmap from `esm.sh` (inline in `tickets/detail.html`) |
| `live-bus.js` | 175 | Global pub/sub `window.LiveBus` |
| `api.js` | 90 | Central API client (CSRF from cookie + meta fallback, session credentials, JSON + multipart) |
| `theme.js` | 77 | light/dark/system (default dark). Loaded SYNCHRONOUSLY in `<head>` (FOUC prevention) |

### CSS

- **`static/css/custom-v15.css`** — **24,934 LOC**. Crimson Black v9 design system. Latest sections:
  - **PROFILE v2** (~478 LOC, `.pf2-*` namespace — hero banner, inline-edit cards, role badges, `pf2-row--flash`; 4 role variants — Team Lead/IT/HR collapse to `--agent`)
  - **AUDIT LOG REDESIGN** (~1,268 LOC, Insights ribbon, heat ribbon, color-coded action chips)
  - **Flatpickr Crimson theme** (globally loaded — all datetime-local inputs themed)
  - **GLOBAL ANIMATION POLISH** (`@keyframes kz-fade-up`/`kz-fade-in`)
  - **INBOX HUB triage cockpit** (rewritten — `.ih-prio-dot--{urgent,high}`, `.ih-actionbar`/`.ih-btn-caret`, `.ih-menu*` floating assign menu + `@keyframes ihMenuIn`, `.ih-email-head/-main/-subject/-from/-when`, `.ih-avatar`, `.ih-row-avatar--{known,new}`/`.ih-row-main/-snippet/-clip`, `.ih-nav-foot`, `.ih-sla-badge--{info,warning,danger}`, and a full **`.ih-context-*` customer-context card** block (head/avatar/id/name/badge/meta/stats/tickets/`.ih-warn`/`.ih-tkt-*`), `body.ih-page` full-viewport grid). Fully tokenized, zero new hex.
  - Token scales (committed 3c99a85): `--crm-font-size-*`, `--crm-weight-{normal:400,medium:500,semibold:600,bold:700}`, `--crm-z-{base,sticky,dropdown,modal,popover,tooltip,flyout:1080,overlay:1085,toast:1090}`, `--crm-radius-{xs:4px,sm:8px,md:6px,lg:14px,xl:16px,pill:9999px}`
- **`static/css/custom.css`** — 20,431 LOC committed snapshot, NOT loaded, allowlisted in theme check.

### Theming Architecture

- **`apps/tenants/colors.py`** (159 LOC) derives **~21-key palette** from primary+accent hex (defaults `#C1121F`/`#E11D2D` if `TenantSettings` fields unset/malformed): 50–900 scale, hover/active/dark/light/subtle/ring/rgb variants, **`text_on_primary`/`text_on_accent`** picked by WCAG 2.x luminance (white vs `#0B0B0B`, whichever wins ≥ 4.5:1). `logger.warning` if final contrast < AA.
- **`base.html` palette block** emits ~35 CSS vars on `:root, [data-bs-theme="light"], [data-bs-theme="dark"]`.
- **No hex literals in rule bodies.** Token blocks are the only place hex is permitted. **`make theme-check`** enforces against `scripts/.theme_baseline.json`.
- **JS color dicts** use `'var(--crm-*)'` literals. **`withAlpha(color, percent)`** uses `color-mix()` for var() inputs, falls back to hex+suffix concat.
- **Dashboard chart color map**: `STATUS_COLORS`/`PRIORITY_COLORS` route through `--status-*-dot` tokens. **Chart.js** can't resolve var(); charts use `cssVar()` + `resolveColor()`.

**Theme baseline** (`scripts/.theme_baseline.json`): **147 hex literals across 11 files** (custom-v15.css 81 + landing_crm.html 21 + dashboard.html 13 + landing.html 15 + contacts/list.html 5 + kanban/board.html 4 + auth/verify_email_sent.html 3 + tickets/detail.html 2 + login.html 1 + reminders/list.html 1 + tickets/list.html 1). Allowlist files: `apps/tenants/colors.py`, `scripts/check_theme.py`, `templates/pages/settings/tenant.html`, `static/js/theme.js`, `static/css/custom.css`. Theme-check masks CSS comments + `:root`/`[data-bs-theme]` blocks + HTML `{% %}` + `<input type="color">`; **inside `<script>` blocks body code is visible** (since `22f284a`); `.js` files flag string-literal hex.

### Templates (48 .html files total)

- `templates/base.html` (265 lines) — palette `<style>`, toast container, quick-notes panel, softphone (conditional), DOMPurify 3.2.4 CDN + SIP.js 0.21.2 CDN (conditional) + Flatpickr 3-CDN-fallback loader + mobile detection IIFE + synchronous `kanzan_sidebar_collapsed` localStorage pre-paint. Default theme: dark.
- `templates/includes/` (6 files): `navbar.html` (189 lines, `#liveStatusPill`, availability dropdown, notification cluster `.notif-flyout` 3s auto-fade); `sidebar.html` (Inbox Hub link FIRST in Inbox section + `#sidebarBadgeInboxHub`, Emails uncommented, avatar bg-image); `softphone.html` (169 lines, conditional); `messages.html` (21 lines); `kb_sidebar_widget.html` (158 lines — **ORPHAN**, safe-deletion candidate); `page_back_button.html` (`sidebarPaths` 11 entries incl. `/inbox-hub/`; included by 17 page templates).
- `templates/pages/` — **18 subfolders + 8 root files**. Subfolders: agents/, analytics/, audit_log/, auth/, billing/, contacts/, emails/, groups/, inbound_email/, inbox_hub/, kanban/, knowledge/, messaging/, reminders/, settings/, tickets/, users/, voip/. Root: 403, api_quickstart, calendar, dashboard, landing, login, profile, register.
- `templates/landing/landing_crm.html` (1,393 LOC) — standalone marketing page. Email templates (12 files / 6 pairs).

### Profile v2 page (`templates/pages/profile.html`, 444 LOC)

`.pf2-*` namespace. Hero + 3 cards. Inline-edit fields routed by `data-target` to `PATCH /accounts/users/{id}/` (first_name/last_name/phone) or `PATCH /accounts/profiles/me/` (job_title/department/bio); email locked. Avatar upload via `Api.upload('/accounts/profiles/upload-avatar/')` (2MB cap, MIME check). **No modals** — Enter saves, Esc cancels. **Role badge** = 1 of 4 variants (`--admin/--manager/--agent/--viewer`) — Team Lead/IT/HR collapse to `--agent`.

### Reminders v2 page (`templates/pages/reminders/list.html`, 3,635 LOC)

Split-pane workspace. State `{page, totalPages, mineOnly, status, priority, quickFilter, search, selectedId, selectedReminder, mode:'empty'|'view'|'form', formIsEdit, items, checked:Set()}`. Mode machine `showEmpty/showView/showForm`. **Global keybinds `N` and `Esc` only** — `Enter` is bound per-input on the two quick-add fields (NOT global). 5 group buckets (overdue/today/upcoming/done/cancelled) + chip rail doubling as presets. Workload banner (4 tones). Bulk bar (complete/reschedule/cancel; `__bulk__` sentinel). Endpoints `/crm/reminders/` + `/stats/`/`/complete/`/`/reschedule/`/`/cancel/`/`/bulk-action/`. LiveBus: 5 verbs + `live.reconnected`, debounced 500ms.
- **NEW natural-language quick-add parser** (`parseQuickAddTime` + `cleanSubject`, ~120 LOC): "call Acme tomorrow 4pm urgent" → parses relative offsets ("in 3h/30 mins/2 days/1 week"), day anchors (today/tonight/tomorrow/weekday + "next"), clocks (am/pm, 24h, noon/midnight/morning/afternoon/evening), priority words (urgent/asap/critical→urgent, high, low, `!`→high `!!`→urgent), then strips matched spans from the subject. Ignores bare "at 4" (ambiguous); clock-only-in-the-past rolls to tomorrow.
- **Layout refresh**: summary is now a **5-column CSS grid** of stat cards (`rm-summary`, responsive 3/2 cols) inside a page hero; a **body-portaled "Filters" popover** (`buildFilterPop`/`openFilterPop`) collapses the status+priority selects into chips (`.rm-fchip`) with active-count badge + chip↔select sync + close-on-outside/Esc/resize/scroll. Adds 59 `rgba()` literals (not flagged by the hex-based theme check).

### Groups smart member picker (`templates/pages/groups/list.html`, 734 LOC)

`getOtherGroupMemberMap()` → `{user_id: group_name}` for users in any OTHER group; picker excludes them unless already in the edited group. Legacy multi-group users flagged `.bg-danger-subtle` + chip; Save disabled when conflicts selected. **Server enforcement** in BOTH `UserGroupSerializer.validate_member_ids` and `UserGroupViewSet.add_members` — returns a **flat concatenated string in `detail`**, NOT a `{conflicts:[...]}` array.

### Audit log page (`templates/pages/audit_log/list.html`)

Two tabs (`pane-activity` + `pane-inbound-email`). Insights redesign: hero `#statTotal` + stat tiles, top-contributor + risk-signal blocks, 7-day heat ribbon, filter chip rail, dropdown panel, day-grouped timeline, side drawer with prev/next. `dedupTimeline()` merges near-dupes within 2s keyed by `(action, object_id)`. **Export fix (working tree):** CSV/JSON export now (1) mirrors the live filters (action chips via `selectedActions` → `action`/`action__in`, plus `mineOnly`) and (2) walks every paginated `next` link via `fetchAllLogs()` (MAX_PAGES=200, same-origin) — replacing the old `page_size=1000` (which the endpoint ignored, silently truncating to 50 rows). New shared `triggerDownload()`; toasts report exported row count.

### Kanban board page (`templates/pages/kanban/board.html`)

Filter panel teleported to `<body>` at z-index 1085. **SortableJS 1.15.2** column DnD; `onEnd` posts to `CardPositionViewSet.move/reorder` passing `actor=request.user, request=request` → cross-status drags route through `apps.tickets.services.change_ticket_status`.

### Settings hub (`templates/pages/settings/tenant.html`, ~5,116 lines)

Searchable hub with ~20 panes incl. Developer & Integrations (apiKeysPane + apiDocsPane). Email-automation pane now carries 2 Inbox Hub toggles (`#inboxHubEnabledToggle`, `#inboxHubAutoAssignToggle`). API-keys pane: form + 3-filter table + 3 modals.

### Context Processor (`apps/tenants/context_processors.py`, 81 lines)

Injects: `tenant`, `membership`, `user_role` (= `effective_role`), `is_admin`, `is_admin_or_manager`, `is_agent_or_above`, **`voip_enabled`**, **`tenant_palette`** (~21-key), `BASE_URL`. Caches membership on `request._cached_tenant_membership`.

## Middleware Stack (14 layers)

1. SecurityMiddleware
2. WhiteNoiseMiddleware
3. SessionMiddleware
4. CorsMiddleware
5. CommonMiddleware
6. CsrfViewMiddleware
7. AuthenticationMiddleware
8. AccountMiddleware (allauth)
9. **SessionVersionMiddleware** (custom — global logout via `User.auth_version`)
10. **TenantMiddleware** (tenant resolution + async-safe context; `/admin/` has dedicated branch)
11. **SubscriptionMiddleware** (billing enforcement — HTTP 402 when neither `is_active` nor `in_grace_period`)
12. **RateLimitHeadersMiddleware** (`apps.api_keys.middleware` — emits `X-RateLimit-*` from `request._kanzan_throttle_info`)
13. MessageMiddleware
14. XFrameOptionsMiddleware

## REST Framework Config (`main/settings/base.py`)

```python
DEFAULT_AUTHENTICATION_CLASSES = [JWTAuthentication, APIKeyAuthentication, SessionAuthentication]
DEFAULT_PERMISSION_CLASSES = [IsAuthenticated]
DEFAULT_FILTER_BACKENDS = [DjangoFilterBackend, SearchFilter, OrderingFilter]
DEFAULT_THROTTLE_CLASSES = [ScopedRateThrottle, apps.api_keys.throttling.APIKeyRateThrottle]
DEFAULT_THROTTLE_RATES = {auth: 10/min, api_default: 200/min, api_heavy: 30/min, webhook: 60/min, api_key: 1000/hour}
PAGE_SIZE = 50
SCHEMA = drf-spectacular AutoSchema
RENDERERS = [JSONRenderer, BrowsableAPIRenderer]
```

**Only `AuthViewSet` sets `throttle_scope = "auth"`** — `api_heavy`/`webhook` defined but never opted into. `APIKeyRateThrottle` opt-in-free: auto-engages when `request.auth` is an `APIKey`; stashes `(limit, remaining, reset_epoch)` on BOTH `request._kanzan_throttle_info` and `request._request._kanzan_throttle_info`.

## Third-Party Integrations (selected)

| Integration | Version | Purpose |
|-------------|---------|---------|
| Django | 6.0.2 | Framework |
| DRF | ≥3.16,<4 | REST API |
| Channels | ≥4.2,<5 | WebSocket |
| channels-redis | ≥4.2,<5 | Channel layer |
| Celery | ≥5.4,<6 | Background tasks |
| django-celery-results | ≥2.5,<3 | Celery result store |
| django-redis | ≥5.4,<6 | Cache + sessions |
| psycopg | ≥3.2,<4 | PostgreSQL driver |
| Stripe | ≥11,<12 | Payments |
| django-allauth | ≥65,<66 | OAuth2 SSO |
| SimpleJWT | ≥5.4,<6 | JWT auth |
| DRF-Spectacular | ≥0.28,<0.29 | OpenAPI 3.0 docs |
| django-filter | ≥24.3,<25 | API filtering |
| django-cors-headers | ≥4.6,<5 | CORS |
| django-environ | ≥0.12,<1 | Env config |
| WhiteNoise | ≥6.8,<7 | Static serving |
| python-magic | ≥0.4,<0.5 | MIME detection |
| Pillow | ≥11,<12 | Image processing |
| mammoth | ≥1.12,<2 | `.docx` → HTML |
| openpyxl | ≥3.1,<4 | Excel export (optional) |
| aiosmtpd | ≥1.4,<2 | In-process SMTP server |
| httpx | ≥0.27,<1 | Async HTTP for ARI |
| websockets | ≥12,<14 | WebSocket client for ARI |
| SIP.js | 0.21.2 (CDN) | Browser SIP/WebRTC |
| Bootstrap | 5.3.3 (CDN) | CSS framework |
| Tabler Icons | 3.31.0 (CDN) | Icon webfont |
| DOMPurify | 3.2.4 (CDN) | XSS sanitization |
| Flatpickr | latest (3-CDN fallback) | Date pickers |
| Chart.js | 4 (page-specific CDN) | Dashboard trends |
| SortableJS | 1.15.2 (page-specific CDN) | Kanban DnD |
| TipTap | 2 (esm.sh importmap, page-specific) | Rich text editor |
| Jazzmin | ≥3.0,<4 | Admin theme |
| daphne | ≥4.2,<5 | ASGI server |
| gunicorn | ≥25,<26 | WSGI/ASGI server |
| uvicorn[standard] | ≥0.40,<1 | ASGI worker |
| Flower | ≥2.0,<3 | Celery monitoring |

`requirements/base.txt` has ~30 requirement lines. `django-celery-beat` excluded (Django 6 incompat). **`requirements/prod.txt` has zero extras** — just `-r base.txt`. Root **`requirements.txt`** is **byte-identical** to `requirements/base.txt`. `requirements/dev.txt` = `-r base.txt` + 10 dev tools.

## Billing Plans

| Plan | Users | Contacts | Tickets/mo | Storage | API | SSO | SLA | VoIP | Call Recording |
|------|-------|----------|-----------|---------|-----|-----|-----|------|----------------|
| Free | 3 | 500 | 100 | 1GB | No | No | No | No | No |
| Pro | 25 | 10K | 5K | 25GB | Yes | No | Yes | Yes | Yes |
| Enterprise | Unlimited | Unlimited | Unlimited | Unlimited | Yes | Yes | Yes | Yes | Yes |

Plan also has: `has_realtime`, `has_custom_roles`, `max_custom_fields`, `max_calls_per_month`, `audit_retention_days` (NULL = unlimited).

## Management Commands (8 total)

```bash
# Tenancy
python manage.py provision_tenant --name "Acme" --slug acme [--domain crm.acme.com]

# Seeding
python manage.py seed_plans                                    # Free/Pro/Enterprise (idempotent)
python manage.py setup_queues --tenant-slug demo               # 4 default queues
python manage.py setup_ticket_statuses --tenant-slug demo      # 5 default statuses
python manage.py backfill_sla_audit [--tenant-slug] [--dry-run]   # baseline SLA audit
python manage.py seed_inbox_hub_defaults [--tenant-slug <slug> | --all-tenants]  # General dept + memberships (NEW)

# Long-running daemons
python manage.py run_smtp_server                               # kanzan-smtp PM2 process
python manage.py run_ari_listener                              # VoIP Stasis event loop (NOT in PM2)
```

## Environment Variables

### In `.env.example` (16 keys)
`DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DATABASE_URL`, `REDIS_URL`, `BASE_DOMAIN`, `BASE_SCHEME`, `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`, `JWT_SECRET_KEY`, `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `KANZAN_FLOWER_AUTH`. (`.env` itself has 26 keys; `BASE_SCHEME` is in `.env.example` but not `.env` — harmless default.)

### Read by `base.py` but NOT in `.env.example` (23 keys)
- **Base:** `BASE_PORT`
- **IMAP:** `IMAP_HOST`, `IMAP_PORT`, `IMAP_USER`, `IMAP_PASSWORD`, `IMAP_MAILBOX`, `IMAP_USE_SSL`, `IMAP_DEFAULT_TENANT_SLUG`
- **SMTP server:** `SMTP_SERVER_HOST`, `SMTP_SERVER_PORT`, `SMTP_SERVER_HOSTNAME`, `SMTP_SERVER_REQUIRE_AUTH`, `SMTP_SERVER_AUTH_USERS` (JSON dict), `SMTP_SERVER_TLS_CERT_FILE`, `SMTP_SERVER_TLS_KEY_FILE`
- **Inbound webhook (unused today):** `INBOUND_EMAIL_WEBHOOK_SECRET`
- **Email:** `EMAIL_BACKEND`, `DEFAULT_FROM_EMAIL`, `EMAIL_TIMEOUT`, `EMAIL_USE_SSL`
- **Inbox Hub presence/SLA (NEW Phase 1B):** `AGENT_PRESENCE_TTL_SECONDS` (90), `AGENT_PRESENCE_AUTO_ONLINE` (True), `HUB_SLA_WARNING_MINUTES` (15)

> `KANZAN_FLOWER_AUTH` is NOT read by `base.py` — only consumed by `ecosystem.config.js` for Flower's `--basic_auth`.

`main/settings/__init__.py` loads `base.py` then conditionally loads `dev.py` (when `DJANGO_DEBUG=True`) or `prod.py` (with try/except). `pytest.ini` sets `DJANGO_SETTINGS_MODULE=main.settings`.

## Testing

- **Framework:** pytest + pytest-django.
- **Module counts:** **56 root-level** `tests/test_*.py` + **7 app-level** = **63 total**.
- **Config:** `pytest.ini` — `DJANGO_SETTINGS_MODULE=main.settings`, `pythonpath=.`. **No `asyncio_mode`** (defaults to `strict`).
- **Fixtures (`conftest.py`):** **16 factories + 20 fixtures (3 autouse:** `celery_eager`, `free_plan`, `clear_tenant_context`**).** `RoleFactory` declares 4 traits (admin/manager/agent/viewer); tests fetch `Role.unscoped.get(slug=...)` from the signal-seeded set for team-lead/it/hr.
- **Celery:** Eager mode (autouse).
- **on_commit tests** wrap POSTs in `django_capture_on_commit_callbacks(execute=True)` so `transaction.on_commit` callbacks fire under pytest-django's atomic-rollback teardown (used by api_keys tests + the Inbox Hub end-to-end park tests).
- **Inbox Hub:** `tests/test_inbox_hub.py` (**21**) + `tests/test_inbox_hub_routing_assignment.py` (**38**) = **59 passing**; `tests/test_inbound_email.py` (14, incl. internal/mine email-scope) passing; `tests/test_access_control.py` (12) passing. **`tests/test_badges.py` 36/2** — 2 stale fails (14.05/14.09) from the `_message_count` comment→chat repurpose.

## Recent Migration Highlights (116 total; 3 untracked)

| App | Latest | Tracked? | What it adds |
|-----|--------|----------|--------------|
| **agents** | **0007_agentavailability_last_seen** | **UNTRACKED** | `AgentAvailability.last_seen` (db_indexed) — presence heartbeat |
| **tenants** | **0010_tenantsettings_inbox_hub_auto_assign_and_more** | **UNTRACKED** | `inbox_hub_auto_assign` (default True) + `inbox_hub_default_department` FK |
| **inbound_email** | **0010_inboundemail_assignee_and_more** | **UNTRACKED** | `InboundEmail.assignee` FK + index `email_tenant_assignee_idx` — agent email-inbox handoff |
| accounts | 0012_seed_inbox_hub_permissions | tracked | Data migration — 12 inbox_hub codenames via `.add(*perms)` |
| accounts | 0011_seed_team_lead_it_hr_roles | tracked | Backfills Team Lead/IT/HR roles |
| accounts | 0010_user_is_service_account | tracked | `User.is_service_account` |
| accounts | 0009_usergroup | tracked | `UserGroup` model |
| accounts | 0008_add_temporary_permissions | tracked | `TenantMembership.temporary_permissions` M2M |
| agents | 0006_customagentstatus_…_custom_status | tracked | `CustomAgentStatus` + FK |
| api_keys | 0002_rename_*_idx | tracked | Cosmetic index renames |
| comments | 0010_alter_activitylog_action | tracked | ActivityLog actions → **34** (+8 `EMAIL_*`) |
| crm | 0004_reminder_m2m_contacts_tickets | tracked | Reminder FK → M2M |
| inbound_email | 0009_alter_inboundemail_status | tracked | `Status.PARKED_IN_HUB` |
| inbox_hub | 0001_initial | tracked | NEW app, 8 models (5 indexes incl. partial SLA; conditional unique on HubEmailSLA) |
| kanban | 0004_board_is_personal | tracked | `Board.is_personal` |
| knowledge | 0005_article_allowed_groups | tracked | `allowed_groups` M2M |
| messaging | 0002_conversation_source_group | tracked | `source_group` FK |
| notifications | 0005_alter_notification_type_and_more | tracked | NotificationType → **20** (+5 `HUB_EMAIL_*`) |
| tenants | 0009_tenantsettings_inbox_hub_enabled | tracked | `inbox_hub_enabled` (default False) |
| tickets | 0027_queue_department | tracked | `Queue.department` FK → `inbox_hub.Department` (SET_NULL) |
| tickets | 0026_alter_ticketactivity_event | tracked | TicketActivity → 27 |

## Performance Optimizations

- **Analytics closed-status cache** — per-request `_closed_status_cache` in `DashboardView`.
- **Analytics negative-delta guard** — filters `first_responded_at__gte=F("created_at")`.
- **Analytics exclude soft-deleted (NEW working tree)** — all 8 stat querysets in `apps/analytics/services.py` (`get_ticket_stats`, `get_agent_performance`, `get_sla_compliance`, `get_dashboard_summary`, `get_hourly_trends`, `get_unresolved_by_queue`, `get_due_today`, `get_overdue_tickets`) now add `is_deleted=False`, so soft-deleted tickets no longer skew dashboards/reports.
- **Kanban N+1 fix** — `BoardDetailSerializer.get_columns` batch-fetches GenericFK content objects.
- **Comment attachment prefetching** — batch-fetched onto `_prefetched_attachments`.
- **Company `contact_count` annotation** — DB level.
- **SLA breach iteration** — `iterator(chunk_size=200)`.
- **First-response race** — atomic UPDATE + WHERE.
- **Lead/health scoring** — pre-fetches signal sets, iterates chunk_size, bulk-updates by score bucket.
- **Reminder overdue task** — `Reminder.unscoped + iterator(chunk_size=200)`, 1-per-day dedup.
- **HubEmailViewSet `get_queryset`** — `select_related("inbound","contact","department","queue","assignee","escalated_to","converted_ticket")` + `prefetch_related("notes","notes__author")`.
- **AssignmentEngine** — batches candidate load (active HubEmail count) + last-assigned timestamp in 2 queries before sorting.
- **reap_stale_presence / check_hub_sla_breaches** — cross-tenant `.unscoped` + `iterator(chunk_size=200)`.

## Security Hardening

- `IsTenantMember` applied to AttachmentViewSet, BoardViewSet, ColumnViewSet, CardPositionViewSet, ContactGroupViewSet, ConversationViewSet, MessageViewSet, NotificationViewSet, NotificationPreferenceViewSet, QuickNoteViewSet, ReminderViewSet, InboxViewSet, **HubEmailViewSet** (+ all 4 config viewsets) — blocks cross-tenant JWT access.
- **`LiveEventConsumer`** verifies tenant membership before joining channel-layer group. **Caveat:** `Comment.is_internal` events broadcast on tenant-wide live channel; clients expected to filter. Latent information-leak vector.
- ChatConsumer rate limits + tenant-from-scope verification.
- **Webhook `secret`** write-only in serializer; HMAC SHA-256; auto-disable at 10 failures.
- **XSS prevention** — ticket detail uses `textContent`; KB sanitises mammoth output. **Inbox Hub email body** rendered via strict `BODY_SANITIZE_CONFIG` DOMPurify allowlist.
- **Auth throttling** — `AuthViewSet.throttle_scope = "auth"` (10/min). `APIKeyRateThrottle` (1000/hour) auto-engages on `request.auth = APIKey`.
- **File-upload MIME** — python-magic with content-type fallback. 2MB avatar; 25MB general.
- **Attachment cross-tenant** — `validate()` ensures target object belongs to current tenant.
- **Password validation** — full Django `validate_password()`.
- **Global logout via `auth_version`**.
- **InboundEmail immutability** — `linked_at/by` and `actioned_at/by` raise `ValidationError` if changed.
- **IMAP "never backfill"** — poll aborts when UIDVALIDITY/UIDNEXT unparseable.
- **Superuser-only admin** — `SuperuserOnlyAdminSite`.
- **`AgentAvailabilityViewSet.assignable_roles` excludes admin slug**.
- **One user per group per tenant** — enforced client + serializer + viewset.
- **Inbox Hub RBAC** — local `HubEmailPermission` (effective_role) + `IsHubEmailAccessible` row-scoping; config viewsets manager-gated.

## Key Implementation Details

- **Live broadcasts are best-effort + transactional.** `broadcast_live_event` defers via `transaction.on_commit` and swallows exceptions.
- **`effective_role` everywhere** — including `BadgeCountView` and the Inbox Hub permission classes as of Phase 1B.
- **`has_effective_permission` honours `temporary_permissions` intersection**.
- **Ticket number per-tenant sequencing** via dedicated `TicketCounter` (SELECT FOR UPDATE).
- **Signal dedup flag** `_skip_signal_logging`; service-layer functions set this automatically.
- **Kanban drag → ticket service routing** for cross-status drags.
- **`Ticket.save()` auto-populates `company`** from linked Contact's company.
- **`Article.save()` resolves tenant from context** with fallback slug.
- **Session cookie** host-only (Chrome's strict `.localhost` policy).
- **InboundEmail tenant resolution post-parse** — `tenant` nullable.
- **CannedResponse ownership** — creator or Manager+.
- **SavedView default race** — `transaction.atomic() + select_for_update()`.
- **SLA breach flags persisted before notifications** (dedup).
- **Ticket soft delete** — `is_deleted=True`; `?include_deleted=true` shows; POST `restore/` reverses.
- **Knowledge `Article.allowed_groups`** — visibility scope via M2M UserGroup.
- **Inbox Hub presence** — agent online-state driven by `/ws/live/` 25s ping → `last_seen`; reaper flips stale ONLINE→AWAY at 90s TTL; `is_assignable` is the single gate for auto-assignment.
- **Inbox Hub hold/drain** — no online agent → email held NEW; first eligible agent's reconnect drains the department backlog up to capacity.

## Common Pitfalls & Fixes Applied

1. `TenantSettings` dual-PK — removed `primary_key=True`.
2. Allauth config must be a set: `ACCOUNT_LOGIN_METHODS = {"email"}`.
3. All apps need `migrations/__init__.py`.
4. DRF 3.15.2 → 3.16.1 (Django 6.0 compat).
5. `base.html` needs `user.is_authenticated` check.
6. Role creation includes `hierarchy_level`.
7. Ticket stats JS reads `data.ticket_stats`.
8. Flower in requirements/base.txt.
9. **Viewer IS seeded by default** — **7 system roles total**.
10. `swagger_fake_view` check in `get_queryset()`.
11. Use `get_user_model()` in async consumers.
12. Test fixtures: `UserFactory` `_after_postgeneration` with `skip_postgeneration_save = True`.
13. Test base: `current_period_start/end` must be tz-aware.
14. **`django-celery-beat` removed** — Django 6 incompat.
15. **VoIP queue** — `kanzan_voip` not in default worker `-Q`.
16. **PM2 count** — 5 prod. Makefile omits `kanzan-smtp`.
17. **11 Beat tasks** (+`reap-stale-presence`, +`check-hub-sla-breaches`). `check_overdue_reminders` and `check_sla_breach_warnings` exist but NOT scheduled.
18. **CSS versioning** — `custom-v15.css` (live, 24,934 LOC); `custom.css` (snapshot, 20,431 LOC, NOT loaded).
19. **IMAP "never backfill" safety**.
20. **Tenant primary/accent override** supported. Defaults `#6366F1`/`#F59E0B`; fallback Crimson Black.
21. **Reminder M2M** — `contacts`/`tickets` are M2Ms.
22. **Knowledge-base task names** — `knowledge_base.*` namespace.
23. **TicketActivity events** — 27 choices.
24. **ActivityLog actions** — **34 choices** (+8 `EMAIL_*`, all now emitted by Inbox Hub).
25. **Temporary role overrides** — use `effective_role`; honour `temporary_permissions` intersection.
26. **API router include count is 23** (22 unique URLConfs).
27. **Frontend URL count is 35** (incl. `/inbox-hub/`).
28. **91 Django model classes** across 21 apps (raw `^class` grep = 102).
29. **No new hex colour literals in CSS/JS/template rule bodies**.
30. **Use `var(--crm-text-on-primary)`** for text on tenant-themed surfaces.
31. **JS color strings use var() too**.
32. **Hex-alpha concat forbidden** — use `withAlpha(color, percent)`.
33. **Chart.js can't resolve `var()`** — use `cssVar()` + `resolveColor()`.
34. **Live broadcast layer is committed**.
35. **Comment broadcasts ignore `is_internal`** — latent info-leak.
36. **TicketPresenceConsumer `presence_list` unimplemented**.
37. **`UserGroup`** — tenant-scoped; "one user per group" enforced (flat-string `detail` response).
38. **`CustomAgentStatus`** — tenants define custom statuses.
39. **Messaging attachments** — `body` allow_blank; `_broadcast_message` is `@classmethod`.
40. **Status transition relaxation** — `ALLOWED_TRANSITIONS["waiting"]` allows `resolved`/`closed`.
41. **`apps/billing/tasks.*` queue route dormant** — file doesn't exist.
42. **Notification is NOT polymorphic** — `data` JSONField only. 5 polymorphic models: `Attachment, Comment, ActivityLog, CustomFieldValue, CardPosition`.
43. **No CI/CD** — `make check` is pre-commit gate. PM2 single-host deploy.
44. **`BASE_SCHEME` IS in `.env.example`**.
45. **`main/admin.py` full add/change flow** with `TenantFilteredAdmin` tenant picker.
46. **API keys cleartext** `kz_live_<slug6>_<token_urlsafe(32)>` — SHA-512; shown once.
47. **`APIKeyRateThrottle` is `SimpleRateThrottle`-based** — opt-in-free.
48. **`RateLimitHeadersMiddleware`** (slot 12) — read-side only.
49. **`drf-spectacular` OpenAPI extension for API keys** registered via `apps.py::ready()`.
50. **Notification UX** — bell `.is-ringing` ~950ms + `.notif-flyout` 3s auto-fade.
51. **Seven system roles** — admin/manager/team-lead/agent/it/hr/viewer.
52. **`/admin/` NOT in `EXEMPT_PATH_PREFIXES`** — dedicated branch resolves tenant.
53. **DRF auth order is JWT → APIKey → Session**.
54. **Kanban drags trigger full ticket service** when cross-status.
55. **`Ticket.save()` auto-fills `company`** from linked Contact.
56. **`Article.save()` resolves tenant from context**.
57. **`page_back_button.html` wired into 17 pages.** 11-entry array incl. `/inbox-hub/`.
58. **`requirements.txt` byte-identical to `requirements/base.txt`**.
59. **Logs not rotated.** ~74MB; celery-worker-error ~40MB, celery-beat-error ~15MB, django ~14MB. `logs/` is gitignored.
60. **Reminders v2** — Global keybinds `N`/`Esc` only; `Enter` per-input.
61. **CELERY_BEAT_SCHEDULE lives in `main/settings/base.py`**, NOT `main/celery.py`.
62. **Kanban orphan-card cleanup** — `Ticket.post_delete` hard-deletes `CardPosition`. Tickets app has **11 signal receivers**.
63. **CSS token scales** (`3c99a85`) — no magic numbers in rule bodies.
64. **`theme-check` scans inside `<script>` blocks** (`22f284a`).
65. **Inbox Hub seam is flag-gated, OFF by default.** `tenant.settings.inbox_hub_enabled = True` opts in. Reverting restores legacy behaviour for new mail.
66. **`_post_park_hooks` is now FILLED** — route → SLA init → auto-assign (each try/except-isolated; respects `inbox_hub_auto_assign`).
67. **Inbox Hub uses a LOCAL `HubEmailPermission`** (effective_role), NOT the global `ACTION_MAP` — `assign`/`escalate` collide with TicketViewSet.
68. **`ActivityLog.Action` 34** (+8 `EMAIL_*`, all emitted). **`NotificationType` 20** (+5 `HUB_EMAIL_*`, all now created by Phase 1B).
69. **`Queue.department` FK opt-in and nullable** — legacy queue behaviour unaffected.
70. **DO NOT set class-level `queryset = Model.objects.all()` on TenantAwareManager viewsets** — evaluates at import time, returns `.none()` forever. Build fresh in `get_queryset()`.
71. **Profile v2 has only 4 role badge variants** — Team Lead/IT/HR collapse to `--agent`.
72. **Sidebar Emails visible**; sidebar avatar image-aware via server `user.updated` payload.
73. **Groups smart picker** — server response is a **flat string in `detail`**, NOT a `{conflicts:[...]}` array.
74. **`BadgeCountView` rewrites (working tree):** `is_agent` uses `effective_role`; `_email_count` = personal inbox (`assignee=me` ∪ internal-to-me, `inbox_status IN pending/linked`, excl BOUNCED); `_message_count` = **unread CHAT messages** (was unread comments — breaks `test_badges.py` 14.05/14.09); `_inbox_hub_count` = `state=NEW` tenant-wide, 0 if `not can_access_inbox_hub`; `_ticket_count` adds `is_deleted=False` + `agent_visible_tickets_q`.
75. **Inbox Hub FRONTEND is a TRIAGE COCKPIT** (rewritten again, `inbox-hub.js` 1,177 LOC `?v=8`) — 5 workload lenses (all/unassigned/mine/oldest/sla — SLA self-hides at 0), per-email customer-context card (`GET /hub-emails/{id}/context/`), SLA badge + open-ticket nudge, convert/assign/dismiss + floating Assign menu. NOT a "3 priority filter" desk. Backend `claim/escalate/transition/note` exist but the UI never calls them.
76. **⚠️ Inbox Hub access is GROUP-GATED (NEW Jun-9 `apps/inbox_hub/access.py`)** — a member may use the Hub only if **Admin (≤10) OR member of ≥1 `UserGroup`**. Non-Admins in no group: hidden nav (`{% if can_access_inbox_hub %}`), zeroed badge, 403 page (`_inbox_hub_access_required`) + 403 API. This REPLACED department-based row-scoping; even a Manager with all 12 codenames is locked out without a group. Row-scope: ≤20 all rows, agent-tier → `state=NEW OR assignee=me`.
76b. **Agent email-inbox handoff** — manual Hub `assign`/`reassign`/`claim` stamps `InboundEmail.assignee` (+ PENDING/unread) so the original mail shows on `/emails/?assigned=me`; auto-assign does NOT. New `create_ticket` action on `InboundEmailViewSet` (role ≤30).
76c. **⚠️ Agent ticket visibility TIGHTENED (NEW `apps/tickets/access.py`)** — `agent_visible_tickets_q` = `assignee=me OR (created_by=me AND assignee IS NULL)`. A self-created ticket handed off to another agent LEAVES the creator's list/badge/kanban/dashboard/detail view. Shared across 5 surfaces; `IsTicketAccessible` delegates to `agent_can_see_ticket`. Old rule was the looser `created_by OR assignee`.
76d. **`contacts/context.py` (NEW)** — `build_contact_context()` extracted from `ContactViewSet`, enriched (company/account/last_activity), cache prefix `contact_context_v2` (60s, skipped when `exclude_ticket` given); shared with the Hub `context` action.
77. **Inbox Hub presence is heartbeat-driven** — `/ws/live/` 25s ping stamps `AgentAvailability.last_seen`; `reap_stale_presence` (60s) ages stale ONLINE→AWAY at 90s TTL.
78. **Inbox Hub auto-assign is gated by `TenantSettings.inbox_hub_auto_assign`** (default True). False = manual claim only; no online agent = held + drained on reconnect.
79. **3 untracked migrations** (`agents/0007`, `tenants/0010`, `inbound_email/0010`); everything else committed in `8682e80`. `makemigrations --check` clean.
80. **`apps/inbox_hub/routing.py` is the RoutingEngine** (email classification), NOT a Channels WebSocket route — do not confuse it with `apps/tenants/routing.py`.
81. **`process_inbound_email` variable-shadowing** — local `settings` rebinds module-level `django.conf.settings`. Sibling `resolve_tenant_from_address` unaffected.
82. **`resolve_tenant_from_address` Strategy 4** — `IMAP_DEFAULT_TENANT_SLUG` last-resort fallback.
83. **48 `.html` templates / 18 `templates/pages/` subdirs / 14 JS files / 5,262 LOC; `custom-v15.css` 24,934 LOC**.
84. **`Makefile` `logs-django` declared in `.PHONY` but no rule body** — calling errors. Log targets: `logs`, `logs-celery`, `logs-all`. 33 targets total.
85. **`KANZAN_FLOWER_AUTH` is PM2-only** — `base.py` doesn't read it.
86. **`seed_inbox_hub_defaults` seeds ONE "General" Department only** — NOT RoutingRules/SLA/QueueRouting; run before flipping `inbox_hub_enabled`.
87. **Inbox Hub SLA is wall-clock** (no business-hours math) — distinct from the ticket SLA engine.
88. **`apps/inbox_hub/` still has NO `signals.py`/`ready()`** — events fan imperatively from service/routing/assignment functions.
89. **`analytics/services.py` excludes soft-deleted tickets** (`is_deleted=False`) across all 8 stat querysets.
90. **`EXEMPT_PATH_PREFIXES` = 17 literals**; `ROLE_DEFINITIONS` in `defaults.py` has **6 entries** (Viewer seeded as a row but permission-less); `ALL_CODENAMES` = **69**; `TicketViewSet` itself has **31** `@action`s (36 was the all-classes count in `tickets/views.py`).
91. **`tickets/detail.html` REMOVED Delete-Ticket** (button + confirm modal + handlers) and the **macro dropdown** from the comment composer. The DELETE endpoint / soft-delete service still exist server-side; the UI just no longer surfaces them.
92. **`tickets/list.html` stat tabs are now DYNAMIC** — `buildStatusTabs()` injects one tab per tenant `TicketStatus` (from `/tickets/ticket-statuses/`, sorted by `order`, with the status color dot + count) via event delegation on `#ticketStats`. Static tabs remaining: All + Urgent. The hardcoded Open/In-Progress tabs are gone.
93. **`HubEmail.first_responded_at` is read-but-never-written** — `check_hub_sla_breaches` guards on it, but no engine code writes it, so the response-breach always fires on the deadline. `escalate_hub_email` bumps `escalation_count` even on an illegal ESCALATED transition.
94. **2 stale `test_badges.py` tests FAIL** (14.05, 14.09) — they assert the old unread-comments "messages" badge; the working tree repurposed `_message_count` to unread-chat. Intentional behavior change, tests not yet updated. Everything else green; `makemigrations --check` clean.
95. **The Hub `context` action lives on `HubEmailViewSet`, NOT contacts** — deliberately, so it respects `IsHubEmailAccessible` row-scoping (agents can't reach a freshly-parked contact via `/contacts/{id}/context/`). Codename `hub_email.view`.

## Documentation

- `/CLAUDE.md` (this file) — day-to-day source of truth.
- `/docs/README.md` — index; **stale** (predates Inbox Hub; points to `/CLAUDE.md`).
- `/docs/architecture.md` — long-form architecture (Version 1.0, **2026-02-06**; STALE — design rationale only).
- `/docs/ui-consistency-audit.md` (211 LOC, 2026-05-22, `26c989b`) — findings doc; **most recommendations now shipped**. Its self-referenced "baseline 125" / "23,759-line CSS" figures are outdated — current baseline is **147**, CSS is **24,934**.
- `/docs/reference/{codebase-inventory,api-surface,frontend-surface,infra-surface}.md` (189/242/202/199 LOC) — Verified 2026-05-22 @ `ea87bb2` (code state `241e407`). **STALE vs working tree** — do NOT cover Inbox Hub (Phase 1A/1B, triage-COCKPIT frontend, email-inbox handoff, **group-gated access**), the **Jun-9 `access.py` refactor** (`tickets/access.py`, `inbox_hub/access.py`, `contacts/context.py`), Profile v2, Reminders v2, Groups smart picker, presence layer, the new beat tasks, or the 35 frontend URLs / 23 API mounts. They still say 22 API mounts / 34 frontend URLs / 21 apps / accounts mig 0011. CLAUDE.md wins on any disagreement. (Structure is fine; only content is stale — regenerate against current HEAD when convenient.)
- `/scripts/check_theme.py` + `.theme_baseline.json` — regression guard. Baseline total: **147 hex / 11 files**.
- `README.md` — minimal stub (`# Kanzen`).
