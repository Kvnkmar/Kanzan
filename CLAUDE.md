# Kanzen — Project Intelligence

> Last refreshed: **2026-05-17** (post-API-keys merge + bell-flyout UX + throttle base-class swap). Verified against branch `main` @ `fe0ad66` ("feat(api-keys): add quickstart page, sidebar entry, settings tab, and tests") **plus uncommitted working-tree changes**: `apps/api_keys/apps.py` registers the drf-spectacular OpenAPI extension in `ready()`; `apps/api_keys/throttling.py` now subclasses **`SimpleRateThrottle`** (not `ScopedRateThrottle`) so it engages on every API-key-authenticated request without per-viewset opt-in (skips JWT/session traffic by returning `None` from `get_cache_key`); `apps/api_keys/tests/{test_authentication,test_viewset}.py` cleanup + `django_capture_on_commit_callbacks` for the post-commit email task; and a bell-anchored notification flyout (`templates/includes/navbar.html` + `static/js/app.js` + `static/css/custom-v15.css`) replaces the generic `Toast.info` for new notifications — bell rings, badge bumps, peek-preview card auto-fades after 5s. The API-keys feature itself adds: a new `apps/api_keys/` app (model + auth class + viewset + throttle + middleware + drf-spectacular extension + **43 tests across 4 files**), `User.is_service_account` (migration `accounts/0010`), **three new `ActivityLog.Action` choices → total 26** (migration `comments/0009`), a new `/api/v1/api-keys/` router include (count 21 → 22), a new middleware `apps.api_keys.middleware.RateLimitHeadersMiddleware` (middleware count 13 → 14), `DEFAULT_THROTTLE_RATES["api_key"] = "1000/hour"`, a `Developer & Integrations` settings tab + reveal/regenerate/revoke modals, and a `/api/quickstart/` developer-guide page (frontend URL count 33 → 34). For deeper per-domain inventory see `docs/reference/{codebase-inventory,api-surface,frontend-surface,infra-surface}.md` — those reference docs were regenerated 2026-05-11 and DO NOT yet reflect the live broadcast layer, messaging attachments, OR the API-keys feature; refresh as needed.

## Project Overview

Multi-tenant CRM, Ticketing, Knowledge Base and VoIP SaaS. **Django 6.0.2 + DRF 3.16+ + Channels 4.2+ + Celery 5.4+** with Bootstrap 5.3.3 + vanilla JS frontend (SIP.js softphone, TipTap rich editor, DOMPurify sanitization). Row-level multi-tenancy via subdomain routing and **contextvars-based** tenant binding (async-safe). PM2 process management.

**Port:** 8001 (ASGI via Gunicorn + Uvicorn worker) | **Dev DB:** SQLite | **Prod DB:** PostgreSQL
**Redis:** db3 (cache + cached_db sessions, prefix `kanzan`), db4 (Celery broker + django-db result backend), db5 (Channels layer, prefix `kanzan:channels`)
**SMTP in-process server:** 2525 (kanzan-smtp PM2 process)
**Flower:** 5556
**TIME_ZONE:** `Asia/Kuala_Lumpur` (Celery uses UTC; `USE_TZ=True`)

## Quick Reference

```
Superuser:      admin@kanzen.local / Pl@nC-ICT_2024
Django Admin:   http://localhost:8001/admin/   (locked to is_superuser — see main/admin.py)

Tenants:
  DPAP:         http://dpap.localhost:8001      (domain: asmra.shop)
  Meeting:      http://meeting.localhost:8001
  Debug:        http://debug-test.localhost:8001

Flower:         http://localhost:5556 (admin:changeme — KANZAN_FLOWER_AUTH)
API Docs:       http://dpap.localhost:8001/api/docs/
```

## Project Structure

```
/home/kavin/Kanzen/
├── apps/                          # 21 Django apps (apps.nav is URL-only — no models, no AppConfig)
│   ├── accounts/                  # Users (+is_service_account), RBAC + temp-role overrides, invitations, profiles, UserGroups, middleware
│   ├── agents/                    # AgentAvailability + CustomAgentStatus + load-fairness email agent picker
│   ├── analytics/                 # Reports, dashboard widgets, exports, calendar events
│   ├── api_keys/                  # APIKey model + auth class + viewset + per-key throttle + rate-limit-headers middleware + drf-spectacular extension
│   ├── attachments/               # File uploads (polymorphic GenericFK)
│   ├── billing/                   # Stripe billing, plans, subscriptions, webhooks, decorators
│   ├── comments/                  # Comments + Mention + CommentRead + ActivityLog (audit, 26 actions) + LIVE signals
│   ├── contacts/                  # Contacts, Companies, Accounts, Groups, ContactEvent (360°) + LIVE signals
│   ├── crm/                       # Activity + Reminder (M2M contacts/tickets), lead/account scoring + LIVE signals
│   ├── custom_fields/             # EAV custom fields per tenant + sync signals
│   ├── inbound_email/             # SMTP+IMAP ingestion → tickets; agent inbox workflow; auto-assign
│   ├── kanban/                    # Visual boards, columns, polymorphic CardPosition (Board.is_personal added)
│   ├── knowledge/                 # KB articles, categories, search, stale alerts, gap digest, allowed_groups M2M
│   ├── messaging/                 # Real-time conversations (WS); Conversation.source_group; attachments on messages
│   ├── nav/                       # URL-only helper (sidebar badge counts API — no models.py / no AppConfig)
│   ├── newsfeed/                  # Internal announcements, reactions, read receipts + LIVE signals
│   ├── notes/                     # Personal sticky notes (6 colors, pinning)
│   ├── notifications/             # In-app + email notifications + WebSocket (handlers in signal_handlers.py)
│   ├── tenants/                   # Tenant model, middleware, frontend views, frontend_urls, **live broadcast layer**, palette
│   ├── tickets/                   # Core ticketing, SLA + business hours, CSAT, pipelines, macros, webhooks, deals
│   └── voip/                      # Asterisk ARI integration, SIP softphone, call logs, recordings, queues
├── main/                          # Django project root (9 modules — includes admin.py)
│   ├── settings/{__init__,base,dev,prod}.py  # __init__ chooses dev/prod based on DJANGO_DEBUG
│   ├── admin.py                   # SuperuserOnlyAdminSite (locks /admin/ to is_superuser) + TenantFilteredAdmin mixin
│   ├── celery.py                  # Celery app + queue routing (5 globs + default), autodiscover_tasks() (no include list)
│   ├── asgi.py                    # ProtocolTypeRouter: HTTP + WebSocket (6 consumer endpoints — see ASGI section)
│   ├── context.py                 # contextvars-based tenant context (async-safe)
│   ├── models.py                  # TimestampedModel, TenantScopedModel
│   ├── managers.py                # TenantQuerySet, TenantAwareManager, SoftDeleteTenantManager
│   └── urls.py                    # 21 /api/v1/ includes (inbound-email dual-mounted at emails/) + /api/docs/ + frontend URLs
├── templates/                     # 51 files total
│   ├── base.html                  # 254 lines — layout, palette <style>, toast container, notes panel, softphone (conditional), live-bus + live-connection JS, Flatpickr 3-CDN loader
│   ├── includes/                  # navbar (with #liveStatusPill), sidebar (data-current-user-id), softphone, messages, kb_sidebar_widget (orphan)
│   ├── pages/                     # 14 page subfolders + 7 root html files
│   ├── landing/landing_crm.html   # Standalone marketing page (doesn't extend base.html)
│   ├── auth/email/                # verify_email.{html,txt}
│   ├── knowledge/email/           # article_rejected.{html,txt}
│   ├── notifications/email/       # notification.{html,txt}
│   └── tickets/email/             # ticket_created, reply_notification, csat_survey (html+txt)
├── static/
│   ├── css/custom-v15.css         # 23,271 lines (live file referenced by base.html — Crimson Black v9; +API-keys settings tab/modals/quickstart + bell-flyout)
│   ├── css/custom.css             # 20,431 lines (committed snapshot — NOT loaded; allowlisted in theme check)
│   └── js/                        # 13 vanilla-JS modules (incl. NEW live-bus.js + live-connection.js)
├── tests/                         # 54 root pytest modules + 7 app-level (61 total, incl. 4 new api_keys/tests/test_*.py) + tests/base.py legacy scaffold
├── conftest.py                    # 16 factories + 20 fixtures (3 autouse: celery_eager, free_plan, clear_tenant_context)
├── pytest.ini                     # DJANGO_SETTINGS_MODULE=main.settings; pythonpath=. (3 lines, no asyncio_mode set)
├── requirements/{base,dev,prod}.txt   # prod.txt is literally `-r base.txt` — no extras
├── ecosystem.config.js            # PM2 prod: 5 processes (.venv/)
├── ecosystem.dev.config.js        # PM2 dev: 4 processes (env/ symlink to .venv/) — no SMTP, watch-mode reloads
├── Makefile                       # 33 targets — dev/start/stop/migrate/test/theme-check/smoke/lint
├── docs/                          # README + architecture.md (stale 2026-02-06) + reference/{4 docs} (last regen 2026-05-11)
├── tmp/emails/                    # Dev email capture (filebased EmailBackend)
├── logs/                          # PM2 log files (one per process, error+out)
├── media/                         # User-uploaded: tenants/{id}/… and inbound_emails/{id}/…
├── scripts/                       # check_theme.py + .theme_baseline.json (theme-leakage regression guard)
├── db.sqlite3                     # Dev database (~12MB)
├── celerybeat-schedule            # Celery Beat shelve file (built-in scheduler — django-celery-beat removed for Django 6 compat)
└── .env                           # 26 keys (not committed); .env.example covers only 16
```

## Multi-Tenancy Architecture

### Three-Layer Isolation

1. **TenantMiddleware** (`apps/tenants/middleware.py`): Resolves tenant from subdomain (`{slug}.localhost`) or `TenantSettings.domain`. Sets `request.tenant` and binds context. Exempt paths include `/admin/`, `/static/`, `/media/`, `/api/v1/accounts/auth/`, `/api/v1/billing/plans/`, `/api/v1/billing/webhook/`, `/api/docs/`, `/accounts/`, `/login/`, `/register/`, `/logout/`, `/verify-email/*`, `/setup-company/`, `/workspaces/`.

2. **TenantAwareManager** (`main/managers.py`): Default `objects` manager auto-filters by `get_current_tenant()`. Returns **empty queryset** when no tenant in context (prevents leakage in admin/Celery — logs `TenantAwareManager: no tenant context for %s` at DEBUG). Use `Model.unscoped` for cross-tenant queries.

3. **TenantScopedModel** (`main/models.py`): Abstract base. UUID PK + Timestamped + `tenant` FK (CASCADE, editable=False, db_index=True). `objects = TenantAwareManager()`, `unscoped = models.Manager()`. Overridden `save()` auto-assigns `tenant` from context; raises `ValueError` if no tenant is bound and none provided. `SoftDeleteTenantManager` adds `is_deleted=False` filter on top.

### Async-Safe Tenant Context (`main/context.py`)
```python
set_current_tenant(tenant)     # Set in middleware / task
get_current_tenant()           # Used by managers & models
clear_current_tenant()         # Cleanup in finally block
with tenant_context(tenant):   # Context-manager form (preferred for tasks)
    ...
```
Uses `contextvars.ContextVar` named `"current_tenant"` — safe across asyncio tasks and Channels consumers.

### Superuser Admin Lock (`main/admin.py`)

Replaces `admin.site.__class__` with `SuperuserOnlyAdminSite` so non-superusers are 403'd from `/admin/` regardless of `is_staff`. Also exposes `TenantFilteredAdmin` mixin — model-admins inheriting it auto-filter by `request.tenant` using `Model.unscoped` when available.

## Live Broadcast Layer (uncommitted, 2026-05-14/15)

A unified pub/sub real-time layer that fans server-side mutations into a single per-tenant WebSocket and a client-side `LiveBus`. Adds **one new WebSocket consumer** (`/ws/live/`) and bus-publishes events from 5 apps' signal handlers. Coexists with — does not replace — the existing per-domain consumers (chat, notifications, ticket-feed, presence, voip).

### Backend

- **`apps/tenants/live.py`** — `broadcast_live_event(tenant, event, payload, *, immediate=False)`. `tenant` may be a model instance OR a raw pk (lets `accounts.signals.broadcast_user_save` fan out from a `values_list` without N+1 fetches). Group: `live_tenant_{pk}`. Wire shape: `{type:"live_event", event:"<domain>.<verb>", payload:{...}, ts:ISO8601}`. Defers via `transaction.on_commit` so a rolled-back write never leaks an event (unless `immediate=True`). Wraps `group_send` in try/except and swallows failures (live updates are best-effort; the user write must not break).
- **`apps/tenants/consumers.py`** → `LiveEventConsumer(AsyncJsonWebsocketConsumer)`. `GROUP_PREFIX="live_tenant"`. Anonymous → close 4001. No tenant on scope → close 4001. Non-member → close 4003 (membership verified via `TenantMembership.objects.filter(user=, tenant=, is_active=True).exists()` even for valid JWTs). Inbound messages silently ignored (read-only channel). Forwards group events as `{type, payload, ts}`.
- **`apps/tenants/routing.py`** → `re_path(r"ws/live/$", LiveEventConsumer.as_asgi())`.
- **`main/asgi.py`** — `live_ws` appended to `URLRouter(messaging_ws + notification_ws + ticket_ws + voip_ws + live_ws)`.

### Signal Emitters

| App | File | Receivers (model.signal → publisher) | Verbs |
|-----|------|--------------------------------------|-------|
| `accounts` | `signals.py` (MODIFIED) | `TenantMembership.post_save/delete`, `Profile.post_save`, `User.post_save` (fans across every active membership) | `membership.created/updated/deleted`, `profile.created/updated`, `user.updated` |
| `comments` | `signals.py` (NEW) | `Comment.post_save/delete` | `comment.created/updated/deleted` (payload `content_type="app_label.model"`, `object_id`) |
| `contacts` | `signals.py` (NEW) | `Contact`, `Company`, `Account`, `ContactGroup` × `post_save/delete` (ContactEvent intentionally skipped — too noisy) | `contact.*`, `company.*`, `account.*`, `contact_group.*` (created/updated/deleted) |
| `crm` | `signals.py` (NEW) | `Activity`, `Reminder` × `post_save/delete`. Reminder verb resolved by state: cancelled_at→`reminder.cancelled`, completed_at→`reminder.completed`, created→`reminder.created`, else `reminder.updated` | `activity.*`, `reminder.*` |
| `newsfeed` | `signals.py` (NEW) | `NewsPost.post_save/delete`, `NewsPostReaction.post_save/delete` | `newsfeed.created/updated/deleted`, `newsfeed.reacted` (with `added: bool`) |

App configs (`apps/{accounts,comments,contacts,crm,newsfeed}/apps.py`) now import their `signals` module in `ready()`.

**Tickets do NOT broadcast server-side to `live_tenant_*`** — `apps/tickets/signals.py` is unchanged in the working tree; `apps/tickets/services.py::broadcast_ticket_event` still publishes only to `ticket_feed_{tenant_id}`. The bridge to LiveBus is **client-side** in `static/js/ticket-feed.js` (normalises `ticket_created` → `ticket.created`, plus an aggregated `ticket.event`). Authenticated tenant pages therefore hold **two concurrent WebSockets** (`ws/live/` + `ws/tickets/feed/`) plus notifications.

### Frontend

- **`static/js/live-bus.js`** (175 lines) — global `window.LiveBus`. API: `on(eventType, handler) → off`, `onMany(arr, handler) → off`, `publish(eventType, payload, opts)`, `debounce(fn, ms)`, `rafBatch(fn)`, `isConnected(channel)`, `setChannelState(channel, state)`. Wildcard `"*"` subscriber receives every event. Cross-tab fan-out via optional `BroadcastChannel('kanzan-live')` (silent fallback). Handler errors caught + logged; never break siblings.
- **`static/js/live-connection.js`** (206 lines) — global `window.LiveConnection`. Single shared `wss?://host/ws/live/`. Skips pre-auth pages (`/login/`, `/register/`, `/verify-email/`, `/verify-email-sent/`, `/auth/handoff/`, `/landing/`, `/setup-company/`, `/workspaces/`) and pages without a Django `sessionid` cookie. Exponential backoff 1s→30s with ±20% jitter, **infinite retries**. 25s heartbeat ping with 8s pong timeout (any inbound message counts as pong). On reconnect: publishes `live.reconnected` so subscribers can refetch to fill gaps. Tab-visibility hook: regaining focus while closed forces immediate reconnect (clears backoff).
- **Wiring in `templates/base.html`** — script load order (lines 141–158): Bootstrap → DOMPurify → `live-bus.js` (always) → `api.js` → `app.js` → `command-palette.js` → `custom-select.js` → **conditional on `tenant and user.is_authenticated`**: `live-connection.js` → `agent-availability.js` → `notes-panel.js` → `keyboard-shortcuts.js` → `ticket-feed.js` → (if `voip_enabled`) SIP.js CDN + `voip-softphone.js`.
- **Live-status pill in `templates/includes/navbar.html` lines 24–31** — `#liveStatusPill` + `#liveStatusDot` + `#liveStatusLabel`. Hidden by default; surfaced by `app.js::initLiveStatusPill()` (lines ~520–582) when ANY tracked channel (`live`, `notifications`, `ticket_feed`) was previously open and is now reconnecting/closed. States: hidden (open), `--reconnecting` (yellow), `--offline` (red).
- **`app.js::initSidebarUserLive()`** (lines 593–610) subscribes to `user.updated`, filters by `data-current-user-id` on `.sidebar-user`, mutates `#sidebarUserName/Email/Avatar`.
- **`ticket-feed.js`** continues to own `ws/tickets/feed/` but also `LiveBus.publish('ticket.<verb>', …)` for sidebar/dashboard subscribers (event names normalised: `ticket_created` → `ticket.created`, plus aggregated `ticket.event`). Banner publishes `ticket.show_pending {count}` when user clicks Show.

### Event Naming

`<domain>.<verb>` — domains observed in code: `user`, `membership`, `profile`, `comment`, `contact`, `company`, `account`, `contact_group`, `activity`, `reminder`, `newsfeed`, `ticket` (client-side normalisation), `notification` (from notification WS bridge), `live` (system event `live.reconnected`), `livebus` (internal channel-state events `livebus.channel_state`).

### Frontend subscribers (where each event drives UI)

| Page | Events | Handler |
|------|--------|---------|
| `dashboard.html` | `ticket.event`, `notification.received`, `newsfeed.*`, `live.reconnected` | Debounced 600ms refresh of stats + recent activity; refetch newsfeed; targeted refetch on SLA/overdue/reminder notification types |
| `tickets/list.html` | `ticket.created`, `ticket.updated/.assigned/.closed/.deleted`, `ticket.show_pending` | Debounced reload (page 1 only) |
| `tickets/detail.html` | `comment.*` (filtered by `content_type=="tickets.ticket"` + matching `object_id`), `ticket.updated/.assigned/.closed`, `ticket.deleted`, `live.reconnected` | Refetch ticket/comments/activity; toast on deletion |
| `contacts/list.html` | `contact.*`, `company.*`, `account.*`, `contact_group.*` (12 events), `live.reconnected` | Debounced 500ms list reload; reopen/close detail panel as needed |
| `reminders/list.html` | `reminder.*` (5 verbs), `live.reconnected` | Debounced 500ms refetch reminders + stats |
| `app.js (global)` | `user.updated`, `livebus.channel_state` | Sidebar live updates; live-status pill |

All page subscribers use a `document.visibilityState !== "hidden"` guard + `visibilitychange` listener so hidden tabs don't burn requests but catch up on focus.

### Channel-Layer Groups (incl. existing)

- `live_tenant_{tenant_id}` — **NEW**, primary live events (newsfeed, CRM, memberships, contacts, comments)
- `notifications_{user_id}` — in-app notifications (existing)
- `chat_{conversation_id}` — chat messages (existing — now carries `attachments` field; see Messaging Attachments)
- `ticket_feed_{tenant_id}` — ticket lifecycle (existing; client republishes into LiveBus)
- `ticket_{ticket_id}_presence` — agent presence on a ticket (existing)
- `voip_{tenant_id}` — call state (existing)

### Close codes (every consumer)

| Code | Where | Reason |
|------|-------|--------|
| `1000` | client `live-connection.js` | Manual clean close on `beforeunload` |
| (default) | `NotificationConsumer`, `CallEventConsumer` | Anonymous or no tenant |
| `4001` | `ChatConsumer`, `TicketPresenceConsumer`, `TicketListConsumer`, `LiveEventConsumer` | Anonymous user or missing tenant |
| `4002` | `ChatConsumer` | Invalid `conversation_id` UUID |
| `4003` | `ChatConsumer` (not a participant), presence/list/live consumers (not a tenant member) | Forbidden |
| `4004` | `ChatConsumer` | Conversation belongs to a different tenant than the Host header |

## Messaging Attachments (uncommitted, 2026-05-14/15)

The messaging app gained an attachment workflow. Code changes are minor but the behavioural contract shifted:

- **`MessageCreateSerializer.body`** is now `CharField(allow_blank=True, required=False, default="")`. An attachment-only message is valid at the serializer layer — the **frontend** must still block a fully-empty send (no body AND no attachments).
- **`MessageSerializer.attachments`** is a new `SerializerMethodField`. Uses `_prefetched_attachments` if set, else fetches via `Attachment` GenericFK by ContentType(Message).
- **`MessageViewSet.broadcast`** (NEW) — `POST /api/v1/messaging/conversations/{conv}/messages/{msg}/broadcast/`. **Author-only.** Re-emits the message over the `chat_{conv_id}` group after the client has linked attachments. `_broadcast_message` is now a `@classmethod` that calls `cls._build_attachment_payload(message)` and includes `attachments` + `author_name` (fallback email if name blank) in the Channels payload.
- **`ChatConsumer._create_message`** also includes `"attachments": []` in inbound-sent payloads (forward-compat) and falls back to `email` for `author_name` when full name is blank.
- **`templates/pages/messaging/chat.html`** adds a pending-attachments tray (`#pendingAttachments`, `#messageAttachInput`), `buildAttachmentBlock()` helper (image vs file chip rendering), with `target=_blank` links.

There are NO new WebSocket consumers from this work — chat still goes through `ChatConsumer`. The only new HTTP route is the `broadcast` action.

## Models (83 model classes across 20 apps with models.py — `nav` is URL-only)

> Counted as `class X(<…>)` in `apps/*/models.py`, excluding `TextChoices`, `Manager`, `QuerySet`. Per-app totals: tickets 22, accounts 8, knowledge 6, contacts 5, voip 5, analytics 4, billing 4, comments 4, kanban 3, inbound_email 3, messaging 3, newsfeed 3, agents 2, crm 2, custom_fields 2, notifications 2, tenants 2, **api_keys 1**, attachments 1, notes 1, nav 0.

### Base Models (Abstract)
- **TimestampedModel**: UUID PK + `created_at` + `updated_at`; default ordering `["-created_at"]`.
- **TenantScopedModel**: TimestampedModel + `tenant` FK (CASCADE, editable=False, db_index=True) + auto-filtering.

### Tenants / Accounts

**tenants** (2): `Tenant` (name, slug unique, domain unique nullable, is_active, logo); `TenantSettings` (1:1; auth_method, SSO config, timezone, date_format, branding `primary_color`+`accent_color` with hex validators, `inbound_email_address`, business hours/days, `auto_close_days` (5), `csat_delay_minutes` (60), `auto_transition_on_assign`, `auto_send_ticket_created_email`, **`auto_assign_inbound_email_tickets`** — migration 0008). **Defaults: `primary_color="#6366F1"`, `accent_color="#F59E0B"`** — NOT Crimson Black. The Crimson Black `#C1121F`/`#E11D2D` is only the *fallback* in `apps/tenants/colors.py::derive_palette` when no settings exist or hex parsing fails.

**accounts** (8 — `UserGroup` added in migration 0009; `User.is_service_account` boolean added in migration 0010):
- `User(AbstractUser)` — email-based custom user, UUID PK, `auth_version` (PositiveIntegerField; bumped for global logout), `avatar`, `phone`, `username=None`, **`is_service_account`** (BooleanField, db_indexed; True for the hidden synthetic users minted by `apps.api_keys` — UI filters these out of staff lists).
- `Permission` — **global** (not tenant-scoped). codename unique; nested `Action` TextChoices (view/create/update/delete/assign/export/manage — 7 members).
- `Role(TenantScopedModel)` — M2M `permissions`, `hierarchy_level` (default 100), `is_system`. `unique_together=("tenant","slug")`.
- `Profile(TenantScopedModel)` — UI/agent prefs (theme, density, signature, DND, language, date/time format, sidebar_collapsed, job_title, department, bio). `unique_together=("user","tenant")`.
- `TenantMembership` — NOT TenantScoped (joins user↔tenant); UUID PK. FKs `user`, `tenant`, `role` (PROTECT), `temporary_role`, `temporary_role_granted_by`, `invited_by`. Fields: `temporary_role_expires_at`, `temporary_role_granted_at`, `is_active`. **M2M `temporary_permissions` → Permission** (curated allow-list — empty = full temp role perms; non-empty = intersection of `temporary_role.permissions ∩ temporary_permissions`). Methods: `has_active_temporary_role`, `effective_role`, `get_effective_permissions_qs()`, `has_effective_permission(codename)`. **Always consult `has_effective_permission()` / `get_effective_permissions_qs()` (or `HasTenantPermission`) instead of `effective_role.permissions` directly, so the intersection is honoured.** `unique_together=("user","tenant")`.
- `Invitation(TenantScopedModel)` — token + expires_at + role FK + invited_by; properties `is_expired`, `is_accepted`.
- **`UserGroup(TenantScopedModel)`** (migration 0009) — tenant-scoped named group; M2M `members` → User. Organisational only (no permission grants). Used by `Article.allowed_groups`. **NOT registered in Django admin.**
- `EmailVerificationToken` — pre-membership signup verification.

### Tickets — heaviest app (22 model classes)

`Pipeline`, `PipelineStage`, `TicketStatus` (incl. `pauses_sla`, `is_closed`, `is_default`), `Queue` (`default_assignee`, `auto_assign`), `TicketCategory`, **`TicketCounter`** (NOT TenantScoped; OneToOne tenant; `last_number`; classmethod `next_number(tenant_id)` uses SELECT FOR UPDATE + F-expression), `Ticket` (~64 fields; soft delete via `SoftDeleteTenantManager`; CSAT; deal fields incl. `pipeline_stage`/`account`/`won_at`/`lost_at`/`won_reason`/`lost_reason`; `merged_into`; `auto_close_task_id`; `pre_wait_status`; `tags`+`custom_data` JSON; `follow_up_due_at`, `last_activity_at`; nested `Priority`/`Channel`/`TicketType` TextChoices), `TicketLink` (4 link types + circular guard via `_creates_circular_dependency` BFS), `SLAPolicy`, `EscalationRule`, `BusinessHours` (timezone IANA + schedule JSON; method `weekly_business_minutes`), `PublicHoliday`, `SLAPause` (`Reason`: waiting_on_customer/manual), `TicketActivity` (**27 event choices** after migration 0026 added inbound_call/inbound_call_completed), `CannedResponse` (UniqueConstraint on shortcut when non-empty), `Macro`, `SavedView` (`ResourceType`: ticket/contact; user-or-shared), `TicketAssignment` (immutable audit), `TicketWatcher` (reasons manual/mentioned/commented/cc, is_muted), `TimeEntry` (1–1440 mins, billable, started/ended), `TicketTemplate`, `Webhook` (HMAC SHA-256, 8 EventType members, auto-disable at 10 failures).

> Admin registers 17 of 22 — TicketLink, TicketCounter, Macro, TicketActivity are NOT in admin.

### Contacts (5)
`Account` (CRM account; `mrr`, `health_score` clamped 0–100), `Company` (name unique per tenant, `domain`, `industry`, `size` 4-choice), `Contact` (email unique per tenant, `email_bouncing` indexed, `lead_score` 0–100 nightly, `last_activity_at` indexed, `source` 6-choice), `ContactGroup` (M2M contacts), `ContactEvent` (append-only 360° timeline; `source` 4-choice: ticket/activity/email/manual; intentionally NOT live-broadcast). Latest migration `0005_widen_phone_field`. **ContactEvent NOT registered in admin.**

### CRM (2 — deal fields live on Ticket; NO services.py)
`Activity` (call/email/meeting/task; due_at, completed_at, outcome), `Reminder` (formerly `Recall`; **M2M `contacts`/`tickets`** since migration 0004; priority; `status` is **derived property** of completed_at/cancelled_at/scheduled_at; `unscoped` manager; `ReminderQuerySet.overdue()/pending()/for_user()`; methods `mark_completed()`, `mark_cancelled()`, `reschedule(new_at, note)`).

### Inbound Email (3)
`InboundEmail` extends `TimestampedModel` (NOT TenantScopedModel — tenant nullable, resolved post-parse). `Direction` unified inbound+outbound; `SenderType` (customer/system/agent); `Status` (8 members); `InboxStatus` (4); `InboxAction` (3). Threading: `message_id` (indexed, stored without `<>`), `in_reply_to`, `references`. Idempotency keys: `"in:{tenant_id}:{message_id}"` / `"out:{tenant_id}:{ticket_id}:{message_id}"`. `is_read` indexed (migration 0007). `save()` enforces immutability of `linked_at/by` + `actioned_at/by` once set. `BounceLog` for hard bounces. **`IMAPPollState`** (`uid_validity`+`last_uid` watermark; never-backfill safety) — migration 0008. **Only InboundEmail is registered in admin** — BounceLog and IMAPPollState are not.

### Knowledge (6)
`Category`, `Article` (status: draft/pending_review/published/rejected/flagged; visibility: internal/public; review workflow + `search_vector` Postgres SearchVectorField + GinIndex; PDF/DOCX via mammoth + sanitisation; **`allowed_groups` M2M to UserGroup** — migration 0005; auto-slug with collision suffix via `Article.unscoped` scan), `KBRevision`, `KBVote` (session_key-keyed; unique per article+session), `KBSearchGap`, `KBTicketLink`. **Only Category + Article registered in admin** — the other 4 are not.

### Kanban (3)
`Board` (`resource_type` TICKET/DEAL, `is_default`, **`is_personal`** added in migration 0004 — personal boards are private to creator), `Column` (board, order, optional status FK, wip_limit, color), `CardPosition` (polymorphic GenericFK; unique on column+content_type+object_id).

### Comments / Messaging / Newsfeed / Notifications

**comments** (4): `Comment` (polymorphic GenericFK, threaded via parent, `is_internal`), `Mention`, `CommentRead` (row-existence = read), `ActivityLog` (immutable polymorphic audit, **26 action choices** after migration 0009 — incl. reminder lifecycle + outbound_call_logged/completed + **api_key_created/regenerated/revoked**).

**messaging** (3): `Conversation` (DIRECT/GROUP/TICKET; FK `source_group` to UserGroup added in migration 0002 — dedup per-creator group conversations + surface group name in UI), `ConversationParticipant` (last_read_at, is_muted), `Message` (threaded via parent, mentions M2M, `is_edited`).

**newsfeed** (3): `NewsPost` (announcement/update/celebration/incident/general — 5 categories; pinned/published/urgent), `NewsPostReaction` (6 emoji choices), `NewsPostRead` (NOT tenant-scoped — row existence = read).

**notifications** (2): `Notification` (15 `NotificationType` choices), `NotificationPreference`. **`Notification` is NOT polymorphic** — it has only a `data` JSONField (no GenericFK; older docs that listed it as polymorphic are wrong).

### Agents / Custom Fields / Billing / Analytics / Attachments / Notes

**agents** (2): `AgentAvailability` (online/away/busy/offline + `custom_status` FK; `current_ticket_count`, `max_concurrent_tickets`, `working_hours` JSON, `auto_away_outside_hours`); **`CustomAgentStatus`** (migration 0006 — tenant-scoped custom statuses: slug, label, color via `StatusColor` 8-choice, `color_hex` property maps slug→hex). `BUILTIN_STATUS_SLUGS = frozenset(AgentStatus.values)` constant. **CustomAgentStatus NOT registered in admin.**

**custom_fields** (2): `CustomFieldDefinition` (8 field types × 3 modules ticket/contact/company; M2M `visible_to_roles`), `CustomFieldValue` (EAV — polymorphic; 4 typed value columns with indexes).

**billing** (4): `Plan` (tiered + feature flags incl. `has_voip`, `has_call_recording`, `max_calls_per_month`, `audit_retention_days` — migration 0002), `Subscription` (1:1 Tenant; `in_grace_period` = 7-day grace after past_due; 6 status choices), `Invoice`, `UsageTracker`.

**analytics** (4): `ReportDefinition`, `DashboardWidget` (per-user, nullable for shared), `ExportJob` (CSV/XLSX/PDF; openpyxl optional with CSV fallback; routes to `kanzan_default`), `CalendarEvent` (`color`/`end_date` added in migration 0003; 5 EventType choices). **CalendarEvent NOT registered in admin.**

**attachments** (1): `Attachment` (polymorphic GenericFK; `tenants/{tenant_id}/attachments/YYYY/MM/{filename}`; python-magic MIME detection).

**notes** (1): `QuickNote` (6 colors; pinning, position; per-user).

### API Keys (1)
`APIKey(TenantScopedModel)` — fields: `name`, `service_user` (OneToOne to a hidden synthetic `User` with `is_service_account=True`; CASCADE), `role` (FK PROTECT — drives `HasTenantPermission`), `prefix` (first ~20 chars, indexed for lookup), `hashed_key` (SHA-512 hex; **cleartext never persisted**), `created_by` (PROTECT), `is_active`, `expires_at`, `last_used_at`/`last_used_ip`/`last_used_user_agent`, `request_count`. `unique_together=("tenant","name")`. Property `masked_prefix → "{prefix}…{hashed_key[-4:]}"`. **Cleartext is returned exactly once at creation/regeneration** — never recoverable afterward. Cleartext format: `kz_live_<tenant_slug>_<secret>`. Sister files in the app: `authentication.py` (DRF auth class; `Authorization: Api-Key …`; cross-tenant guard; timing-safe `secrets.compare_digest`; best-effort `last_used_*`/`request_count` update via `.update()` with no signals), `services.py` (mint/regenerate/revoke; each writes an `ActivityLog`; uses `transaction.on_commit` to queue creation email), `throttling.py` (**`SimpleRateThrottle`** subclass — per-`APIKey.pk` bucket, returns `None` for non-API-key auth; rate `1000/hour`), `middleware.py` (`RateLimitHeadersMiddleware` — emits `X-RateLimit-Limit/Remaining/Reset` from `request._kanzan_throttle_info`), `extensions.py` (drf-spectacular `APIKeyAuthScheme`; registered via `apps.py::ready()`), `views.py` (admin-only viewset; reveal/regenerate/revoke + email-task on create via `transaction.on_commit`), `tasks.py` (`send_api_key_created_email_task`).

### VoIP (5)
`VoIPSettings` (singleton via UniqueConstraint; encrypted ARI creds; STUN/TURN; `pjsip_context`; `recording_enabled`/`voicemail_enabled`/`is_active`; `asterisk_use_ssl` + related fields added in migration 0002), `Extension` (sip_username **globally unique**, encrypted password), `CallLog` (direction 3-choice, status 9-choice, `asterisk_channel_id` indexed, FKs to caller/callee Extension + Contact + Ticket; 4 indexes), `CallRecording` (1:1 CallLog; `tenants/{id}/recordings/YYYY/MM/{uuid}.{ext}`), `CallQueue` (5 ACD strategies + M2M Extension members).

### Polymorphic (GenericFK) Models — 5 total
`Attachment`, `Comment`, `ActivityLog`, `CustomFieldValue`, `CardPosition`. **Not** Notification (data JSONField only).

## Role-Based Access Control

**Hierarchy:** Admin(10) → Manager(20) → Agent(30) → **Viewer(40)**.

**Default role seeding (`apps/tenants/signals.py::create_default_roles`)** runs on `Tenant.post_save (created=True)` and seeds **all four** system roles, including Viewer (slug `viewer`, hierarchy_level 40). Older notes that said Viewer is *not* seeded by default are wrong — code IS seeding it. Test fixtures rely on this signal-driven seeding (e.g. `viewer_role` fixture in `conftest.py`).

- `is_admin`: `hierarchy_level ≤ 10`; `is_admin_or_manager`: `≤ 20`; `is_agent_or_above`: `≤ 30`.
- Agent restriction (`level > 20`): sees only own/assigned tickets, linked contacts, filtered kanban cards, own reminders/activities (enforced by `IsTicketAccessible` and per-viewset `get_queryset` filters).
- **Always check `TenantMembership.effective_role`** — temporary role wins until `temporary_role_expires_at`. Used by context processor and permission classes.
- **Permission classes** (`apps/accounts/permissions.py`):
  - `HasTenantPermission` — codename-based (ACTION_MAP maps 70+ DRF action names to `{resource}.{action}`); `apply_macro` → `update`; falls back to hierarchy defaults when the membership has no permissions in its qs (view → ≤40, create/update → ≤30, delete/other → ≤20).
  - `IsTicketAccessible` — object-level row filtering for agents.
  - `IsTenantMember`, `IsTenantAdmin`, `IsTenantAdminOrManager`.
  - Helper `_get_membership()` caches the membership on `request._cached_tenant_membership` for repeated checks within a request.
- `_role_required(20)` decorator gates admin/manager frontend pages (users, billing, agents, audit_log, groups). `_role_required(30)` gates the Outbound Emails page. **`/settings/` is `@_membership_required + @ensure_csrf_cookie`** — any member can load the page; API enforces admin-only writes (with a per-field allowlist for Managers — `auto_transition_on_assign`, `auto_send_ticket_created_email`, `auto_assign_inbound_email_tickets`).

## Signals (10 apps with signals.py + notifications/signal_handlers.py)

Pre-existing committed: **accounts, custom_fields, knowledge, tenants, tickets, voip**. `notifications` uses `signal_handlers.py` (not `signals.py`).
Newly added (uncommitted) for the live broadcast layer: **comments, contacts, crm, newsfeed**.
`accounts/signals.py` was modified in the live work to add membership/profile/user broadcasters.

### Tenants (`apps/tenants/signals.py`)
- `Tenant.post_save (created=True)` → `create_tenant_settings`, `create_default_roles` (Admin/Manager/Agent/Viewer + permissions via `apps.accounts.defaults::ROLE_DEFINITIONS`).

### Accounts (`apps/accounts/signals.py` — MODIFIED for live layer)
- `TenantMembership.post_save` → `create_profile_on_membership` (auto-creates Profile) + `broadcast_membership_save` (`membership.created/.updated`)
- `TenantMembership.post_delete` → `broadcast_membership_delete`
- `Profile.post_save` → `broadcast_profile_save` (`profile.created/.updated`)
- `User.post_save` → `broadcast_user_save` (skips creation; on update fans `user.updated` across every active membership's tenant group via `values_list("tenant_id", flat=True)`)

### Tickets (`apps/tickets/signals.py`) — 10 receivers (unchanged in working tree)
- `Ticket.pre_save` → `handle_ticket_status_change` (set resolved_at/closed_at, stash old values, check resolution breach)
- `Ticket.post_save` → `fire_ticket_created_signal` + `fire_ticket_assigned_signal` (custom Django signals → webhooks + notification handlers)
- `Ticket.post_save` → `log_ticket_activity` (writes ActivityLog; 2-second dedup; skips if `_skip_signal_logging` flag set)
- `Ticket.post_save` → `sync_kanban_card_on_status_change`, `sync_kanban_card_on_pipeline_stage_change`, `create_kanban_card_on_ticket_save`
- `Ticket.post_save` → `handle_sla_pause_on_status_change` (creates/closes `SLAPause` entering/leaving a `pauses_sla` status; shifts deadlines forward by business-adjusted pause duration)
- `@receiver(ticket_closed)` → `check_kb_article_coverage` (flags `needs_kb_article` if category has <3 published articles)
- `SLAPolicy.post_save` → `propagate_sla_policy_change` (recalculates deadlines for affected open tickets; async via Celery if >50)

### Custom Fields (`apps/custom_fields/signals.py`)
- `Ticket.post_save`/`Contact.post_save` → sync `CustomFieldValue` from JSON `custom_data`

### Knowledge (`apps/knowledge/signals.py`)
- `Article.post_save` → `update_search_vector` (Postgres FTS; uses `.update()` to avoid recursion; skips on non-Postgres backends)
- (Article review status transitions write audit log + email notifications — these are wired in views/services, not signals)

### Notifications (`apps/notifications/signal_handlers.py`)
- `@receiver(ticket_assigned)` → `handle_ticket_assigned` (creates Notification, queues email task)
- `@receiver(ticket_comment_created)` → `handle_comment_notification` (+ private `_queue_contact_reply_email`)
- These listen to *custom* Django signals fired from `apps.tickets.signals` — not Django built-ins.

### VoIP (`apps/voip/signals.py`)
- `CallLog.post_save` on terminal status (COMPLETED/MISSED/FAILED/BUSY/NO_ANSWER/VOICEMAIL) → writes `TicketActivity` + `comments.ActivityLog` + queues `process_call_recording`. `_timeline_logged` flag dedup; `_TERMINAL_STATUSES` frozenset at module top.

### Live broadcast signals (NEW)
See [Live Broadcast Layer](#live-broadcast-layer-uncommitted-2026-05-1415).

## Dual-Write Logging

**Two parallel log systems:**
1. **TicketActivity** — human-readable timeline, 27 events. Endpoint: `/api/v1/tickets/tickets/{id}/timeline/`.
2. **ActivityLog** — polymorphic audit trail with diffs+IP, 26 actions. Endpoint: `/api/v1/tickets/tickets/{id}/activity/`.

**Dedup:** ViewSet sets `instance._skip_signal_logging = True` before save; signal checks flag. Use `serializer.instance` (not `self.get_object()`) in `perform_update` so the flag persists. 2-sec window in signal.

**Service layer** (`apps/tickets/services.py`) — every mutation writes to BOTH logs atomically and broadcasts WebSocket events via `transaction.on_commit()`. Public functions: `create_ticket_activity`, `assign_ticket`, `transition_ticket_status`, `change_ticket_status`, `change_ticket_priority`, `log_ticket_comment`, `close_ticket`, `escalate_ticket`, `merge_tickets`, `split_ticket`, `bulk_update_tickets`, `apply_macro`/`render_macro`, `record_first_response`, `transition_pipeline_stage`, `initialize_sla`, `broadcast_ticket_event`, `validate_status_transition`, `resume_from_wait`. **Working-tree change** to `services.py`: `ALLOWED_TRANSITIONS["waiting"]` widened from `["open","in-progress"]` to `["open","in-progress","resolved","closed"]` — Waiting → Resolved/Closed are now legal status moves.

**Webhook service** (`apps/tickets/webhook_service.py`): `deliver_webhook` HMAC SHA-256 (`X-Webhook-Signature`), 10s timeout, auto-disable at 10 consecutive failures. `fire_webhooks(tenant, event_type, data)` dispatches async via Celery. Events: `ticket.created/updated/assigned/closed/reopened/comment`, `sla.breached`, `ticket.escalated` (8 EventType choices).

**Transaction safety:** Notifications + WebSocket pushes + email task queues all defer to `transaction.on_commit()` to avoid orphaned tasks on rollback.

## SLA + Business Hours (`apps/tickets/sla.py`)

Single breach-detection entry point `get_effective_elapsed_minutes()`:
- Resolves per-tenant schedule via `BusinessHours` model (JSON per-day open/close + IANA timezone) or legacy `TenantSettings` flat fields.
- Skips `PublicHoliday` dates entirely.
- Subtracts total pause duration from `SLAPause` records (pause minutes counted in business-hour terms).
- Helpers: `elapsed_business_minutes()`, `add_business_minutes()`, `is_within_business_hours()`, `get_total_pause_minutes()`, `sla_deadline_utc()` — day-skip, holiday-skip, hour-windowing.
- `initialize_sla(ticket)` service seeds `response_deadline` and `resolution_deadline`.
- `_check_first_response_breach` uses atomic UPDATE+WHERE (not save) to avoid races with concurrent responses.

## Inbound / Outbound Email

### Inbound (`apps/inbound_email/`)
- **In-process SMTP server** (`smtp_server.py`) via `aiosmtpd`, launched by `run_smtp_server` management command (PM2 process `kanzan-smtp`). Validates RCPT against active tenants; rejects unknown with 550. Optional STARTTLS and LOGIN/PLAIN AUTH via env-configurable users dict.
- **IMAP poller** (`imap_poller.py`) — shared Gmail-style mailbox; `poll_once()` fetches by UID > watermark (NOT UNSEEN — Gmail marks seen instantly). Driven by `fetch_inbound_emails_task` (Celery Beat, 60s). Disabled when `IMAP_HOST` is blank. **Safety guarantee:** never backfills — aborts the poll if UIDVALIDITY/UIDNEXT can't be parsed (regex extracts bare integers from bracketed `OK` response or untagged_responses dict, with `select_resp` fallback).
- **Tenant resolution** — 3 patterns via `resolve_tenant_from_address`: plus-addressing (`support+{slug}@...`), subdomain routing, custom `TenantSettings.inbound_email_address`. Fallback to `IMAP_DEFAULT_TENANT_SLUG` if configured.
- **Filters** (`filters.py`) run BEFORE tenant resolution: loop detection (sender == `DEFAULT_FROM_EMAIL`), noreply senders, RFC 3834 Auto-Submitted / Precedence: bulk/junk/list, subject patterns. `classify_email()` → `bounce` / `auto_reply` / `loop` / `legitimate`. Bounces write `BounceLog` and flip `Contact.email_bouncing=True`.
- **Threading** (`threading.py`) — `find_existing_ticket` 3-tier priority: In-Reply-To → References (reversed) → subject `[#N]` regex. All queries tenant-scoped. Outbound: `build_thread_headers(tenant, ticket, new_message_id)` reads last 10 related InboundEmails for Message-ID chain.
- **Processing pipeline** (`process_inbound_email_task`, max_retries=3, default_retry_delay=30s, acks_late): `select_for_update` → filter classifier → tenant resolution → idempotency claim → find/create contact → find existing ticket OR create new + init SLA + auto-tag "email" + **maybe auto-assign** (`_maybe_auto_assign` calls `auto_assign_email_ticket()` when `TenantSettings.auto_assign_inbound_email_tickets=True`) → attach files (`inbound_emails/{pk}/`) → queue confirmation email via `transaction.on_commit()`.
- **Agent inbox workflow** (`inbox_services.py`): `link_email_to_ticket`, `action_email` (OPEN/ASSIGN/CLOSE), `ignore_email`. Mutations atomic; linked/actioned timestamps immutable once set (enforced in `InboundEmail.save()`).
- **Utils**: `normalize_message_id`, `normalize_references`, `extract_header` (RFC 2822 folding), `parse_sender`, `strip_quoted_reply` (Gmail/Outlook/Apple Mail).

### Outbound (`apps/tickets/email_service.py`)
- `send_ticket_email()` — single entry point. RFC-compliant Message-IDs; sets In-Reply-To, References, Reply-To.
- Persists an OUTBOUND `InboundEmail` record so future replies can be threaded via Message-ID lookup.
- Dev default backend: `django.core.mail.backends.filebased.EmailBackend` → writes to `tmp/emails/`. Prod: SMTP.
- Legacy wrappers: `send_ticket_reply_email`, `send_ticket_created_email`, `send_csat_survey_email` (dispatched async via `send_ticket_*` Celery tasks).

## Auto-Assign (Inbound Email → Agent)

`apps/agents/services.py::pick_email_agent(tenant)`:
1. Active tenant member with **`hierarchy_level == 30`** (pure Agent — excludes Admin/Manager).
2. Must NOT be OFFLINE; agents with no `AgentAvailability` row are eligible.
3. Pick the one with **fewest open tickets** (load balancing).
4. Tie-break by **least-recently-assigned** (`MAX(TicketAssignment.created_at)`, NULLS FIRST for cold-start fairness).

`auto_assign_email_ticket(ticket)` — atomically saves assignment, writes `TicketAssignment` audit row with note `"Auto-assigned from inbound email (load + fairness)"`, best-effort nudges `AgentAvailability.current_ticket_count` (F-expression). Failures swallowed (auto-assign is convenience, not correctness).

## VoIP

**Architecture:** Asterisk/FreePBX → ARI (REST + WebSocket Stasis events). Django wraps ARI, exposes SIP credentials to browser softphone (SIP.js over WSS), persists `CallLog`/`CallRecording`, links to CRM (`Contact`, `Ticket`).

- **`ari_client.py`** — async `httpx` ARIClient: `originate/hangup/hold/unhold/mute/unmute/redirect/create_bridge/add_channel_to_bridge/start_recording/stop_recording/get_recording_file/get_channel`. `ARIEventListener` connects to `ws(s)://host:port/ari/events?app=kanzan-voip&subscribeAll=true`, exponential reconnect 1–30s.
- **`services.py`** — sync wrappers; `process_ari_event` dispatches ChannelStateChange, Hangup, Destroyed, Hold, Unhold to CallLog updates. `_broadcast_call_event` → group `voip_{tenant_id}`. Billing: `check_call_limit` / `increment_call_usage` against `Plan.has_voip` + `UsageTracker.calls_made`.
- **`consumers.py`** — `CallEventConsumer` (`ws/voip/events/`); emits `call_ringing/answered/ended/hold` to softphone.
- **Management command** `run_ari_listener` — long-running async listener for all active tenants; spawns one `ARIEventListener` per tenant concurrently. **NOT in PM2** by default; start separately when VoIP is live.
- **Softphone** — `templates/includes/softphone.html` + `static/js/voip-softphone.js` using **SIP.js 0.21.2** (CDN, conditional on `voip_enabled`).

## API Architecture

### Authentication
- **API:** JWT (SimpleJWT) — 15min access, 7-day refresh, rotate + blacklist, HS256. **`APIKeyAuthentication`** (`apps.api_keys.authentication`) — `Authorization: Api-Key kz_live_<tenant_slug>_<secret>`. Returns `None` (not a 401) when the header is absent or uses a different scheme so JWT/Session can still try. Fails closed (401) on a valid-format but invalid/revoked/expired/cross-tenant key. Cross-tenant guard: when the Host already resolved to a tenant, the key's `tenant_id` must match. SHA-512 hash + `secrets.compare_digest`. Best-effort `last_used_*` + `request_count` update on each request via `.update()` (no signals).
- **Frontend:** Session auth (Redis-backed cached_db, host-only cookie in dev).
- **SSO:** django-allauth (Google, Microsoft, OpenID Connect) — `ACCOUNT_LOGIN_METHODS = {"email"}` (a set).
- **Global logout:** `User.auth_version` bumped invalidates all prior sessions; `SessionVersionMiddleware` enforces.

### `/api/v1/` Endpoint Map (22 router includes from `main/urls.py`, + 1 dual-mount)
```
/tenants/            TenantViewSet (slug lookup), TenantSettingsViewSet (singleton; per-field Manager allowlist)
/accounts/           AuthViewSet (throttle_scope="auth"), UserViewSet, RoleViewSet, ProfileViewSet, InvitationViewSet, TenantMembershipViewSet, UserGroupViewSet
/api-keys/           APIKeyViewSet (admin-only; mint/list/reveal-once/regenerate/revoke; writes ActivityLog; queues creation-email task via transaction.on_commit)
/tickets/            TicketViewSet (31 custom actions), TicketStatusViewSet, QueueViewSet, TicketCategoryViewSet, SLAPolicyViewSet, EscalationRuleViewSet, CannedResponseViewSet, MacroViewSet, SavedViewViewSet, BusinessHoursViewSet (singleton), PublicHolidayViewSet, TicketTemplateViewSet, WebhookViewSet, CSATSubmitView (public)
/contacts/           ContactViewSet, CompanyViewSet, AccountViewSet, ContactGroupViewSet
/billing/            PlanViewSet (AllowAny), SubscriptionViewSet (singleton + cancel/reactivate), InvoiceViewSet, UsageViewSet, checkout, webhook (CSRF-exempt, Stripe-signed)
/kanban/             BoardViewSet (+detail), ColumnViewSet, CardPositionViewSet (+move/reorder/add-ticket)
/comments/           CommentViewSet, ActivityLogViewSet (read-only)
/messaging/          ConversationViewSet (+add/remove/leave/search-participants), MessageViewSet (+ NEW broadcast action)
/notifications/      NotificationViewSet (+mark_read, unread_count, admin-only cleanup), NotificationPreferenceViewSet
/attachments/        AttachmentViewSet (multipart upload, cross-tenant validated)
/analytics/          DashboardView (APIView), ReportDefinitionViewSet, DashboardWidgetViewSet, ExportJobViewSet, CalendarEventViewSet
/agents/             AgentAvailabilityViewSet (10+ actions incl. grant_temp_role/revoke_temp_role/reactivate), CustomAgentStatusViewSet
/custom-fields/      CustomFieldDefinitionViewSet, CustomFieldValueViewSet (read-only)
/knowledge/          CategoryViewSet, ArticleViewSet (+submit_for_review/approve/reject/record_view/remove_file/preview_file/vote), KBSearchView
/notes/              QuickNoteViewSet
/inbound-email/      InboundEmailViewSet (read-only) + InboxViewSet (link/action/ignore)
/emails/             alias mount of inbound_email.api_urls (namespace="emails_api")
/crm/                ActivityViewSet (+my-tasks), ReminderViewSet (+overdue/stats/complete/cancel/reschedule/bulk-action), PipelineForecastView
/nav/                BadgeCountView (capped at 99 per category)
/newsfeed/           NewsPostViewSet (+react/mark-read/mark-all-read/unread-count; dynamic create/update/destroy admin perms)
/voip/               VoIPSettingsViewSet, ExtensionViewSet, CallLogViewSet (+active/stats), InitiateCallView, CallHoldView, CallTransferView, CallHangupView, SIPCredentialsView, CallRecordingDownloadView, CallQueueViewSet
```

**Non-HTTP inbound channel:** `kanzan-smtp` PM2 process accepts mail on `SMTP_SERVER_HOST:SMTP_SERVER_PORT` (default `0.0.0.0:2525`) and feeds the same `InboundEmail` + Celery pipeline.

**Docs:** `/api/docs/` (Swagger UI), `/api/schema/` (OpenAPI 3.0 JSON).

### TicketViewSet — 31 Custom Actions

Permission stack: `[IsAuthenticated, HasTenantPermission, IsTicketAccessible]`, `permission_resource = "ticket"`.

- **Mutations:** `assign`, `close`, `change_status`, `change_stage`, `escalate`, `restore`, `merge`, `split` (Manager+ gates inside merge/split/bulk-delete)
- **Timeline:** `comments`, `activity`, `timeline`, `mark_all_read`
- **Email:** `emails`, `send_email`, `send_creation_email`, `link_email`, `unlinked_emails`
- **Linking:** `links`, `delete_link` (regex path)
- **Macros & bulk:** `apply_macro` (mapped to `update`; regex path), `bulk_action`
- **Watchers:** `watchers`, `watch`, `remove_watcher`
- **Time:** `time_entries`, `time_summary`, `time_entry_detail`
- **Search:** `lookup` (number-only, ignores soft-delete), `search`, `teammates`, `team_progress` (Manager+ inside)

**Working-tree change to `views.py`:** translates Django `ValidationError` → DRF `ValidationError` on status transition validation so clients get a clean 400 instead of 500.

### Other notable @action surfaces
- **`AgentAvailabilityViewSet`** (apps/agents/views.py): `set_status`, `my_status`, `all_members`, `assignable_roles` (admin), `role_permissions/{role_id}` (admin), `grant_temp_role` (admin), `revoke_temp_role`, `reactivate` (admin), `online`, `workload`. Drives `TenantMembership.temporary_role` + curated `temporary_permissions` overrides.
- **`ReminderViewSet`**: `overdue`, `stats`, `complete`, `cancel`, `reschedule`, `bulk_action`.
- **`InboxViewSet`**: `link`, `action` (url_path; method `take_action`), `ignore`. Agent inbox workflow on `/inbound-email/` and `/emails/`.
- **`NewsPostViewSet`**: `react` (POST upsert / DELETE clear), `mark_read`, `mark_all_read`, `unread_count`. Dynamic permissions via `get_permissions()`.
- **`ArticleViewSet`**: `submit_for_review`, `approve`, `reject`, `record_view`, `remove_file`, `preview_file` (mammoth+sanitiser), `vote`.
- **`WebhookViewSet`**: `test`, `reset_failures`.
- **`ConversationViewSet`**: `add_participant`, `leave`, `search_participants` (bypasses `user.view` perm), `remove_participant`.
- **`MessageViewSet`**: **`broadcast` (NEW, author-only)** — re-emit a message over chat group after attachment linking.

### REST Framework Config
- Authentication: **`APIKeyAuthentication`, `JWTAuthentication`, `SessionAuthentication`** (default — Api-Key checked first; falls through to JWT then Session when header isn't `Api-Key`).
- Pagination: PageNumberPagination, PAGE_SIZE=50.
- Filtering: DjangoFilterBackend + SearchFilter + OrderingFilter.
- Throttle classes (default, applied to every viewset): `ScopedRateThrottle`, `apps.api_keys.throttling.APIKeyRateThrottle`.
- Throttle rates: `auth=10/min`, `api_default=200/min`, `api_heavy=30/min`, `webhook=60/min` (ScopedRateThrottle), **`api_key=1000/hour`** (per-`APIKey.pk` bucket via `SimpleRateThrottle`). **Only `AuthViewSet` actually sets `throttle_scope = "auth"`** — `api_heavy`/`webhook` are defined but never opted into. `APIKeyRateThrottle` does NOT require an opt-in: it engages automatically whenever `request.auth` is an `APIKey` instance, and stashes `(limit, remaining, reset_epoch)` on `request._kanzan_throttle_info` (set on BOTH the DRF `Request` and underlying `HttpRequest`) so `RateLimitHeadersMiddleware` can emit `X-RateLimit-Limit/Remaining/Reset` headers on the response.
- Renderers: JSON + BrowsableAPI.
- Schema: drf-spectacular (`SPECTACULAR_SETTINGS.TITLE="Kanzen Suite API"`). The `apps.api_keys.extensions.APIKeyAuthScheme` is registered by `apps/api_keys/apps.py::ready()` so Swagger UI's "Authorize" button shows an `ApiKeyAuth` option alongside the JWT bearer.

### Public / unauthenticated endpoints
- `POST /api/v1/tickets/csat/` — `CSATSubmitView` (`authentication_classes=[]`, `permission_classes=[]`; signed token validates caller).
- `GET /api/v1/billing/plans/` — `PlanViewSet` with `permission_classes=[AllowAny]`.
- `POST /api/v1/billing/webhook/` — `stripe_webhook` (only `@csrf_exempt` in repo; HMAC validated).
- `AuthViewSet.register/login/accept_invitation` — `[AllowAny]`, `throttle_scope="auth"`.

### Frontend Routes (`apps/tenants/frontend_urls.py`) — **34 paths**
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
/api/quickstart/              api_quickstart_page   @_membership_required (developer API guide; renders templates/pages/api_quickstart.html)
/inbound-email/               inbound_email_page    @_membership_required (agent inbox)
/reminders/                   reminders_page        @_membership_required
/audit-log/                   audit_log_page        @_role_required(20)
/calls/                       calls_page            @_membership_required (VoIP call history)
```

## WebSocket Endpoints (6 total — `main/asgi.py`)

Stack: `ProtocolTypeRouter({"http": django_asgi_app, "websocket": AllowedHostsOriginValidator(AuthMiddlewareStack(WebSocketTenantMiddleware(URLRouter(messaging_ws + notification_ws + ticket_ws + voip_ws + live_ws))))})`.

`WebSocketTenantMiddleware` (`apps/tenants/middleware.py`) decodes the `Host` header from scope, resolves Tenant via subdomain or `domain` field, sets `scope["tenant"]` and binds `set_current_tenant()` for the lifetime of the connection; clears in `finally:`.

1. **Chat:** `ws/messaging/{conversation_id}/` → `ChatConsumer`. Actions: `send_message`, `typing`, `mark_read`. Group: `chat_{conversation_id}`. Limits: 10KB/msg, 5 msg/s, 2s typing cooldown. Validates participant + tenant. **Working-tree change**: outbound `chat_message` payload now includes `attachments: []` and falls back to `email` for `author_name`.
2. **Notifications:** `ws/notifications/` → `NotificationConsumer`. Group: `notifications_{user_id}`. Inbound: `{action: "mark_read", notification_id}`. No tenant verification (user-scoped group).
3. **Ticket Presence:** `ws/tickets/{ticket_id}/presence/` → `TicketPresenceConsumer`. Events: `agent_joined`, `agent_left`. Group: `ticket_{ticket_id}_presence`. Heartbeat support. **Known gap:** docstring mentions a `presence_list` event for newcomers to learn existing viewers; it is **not implemented** — newly joined clients only see their own `agent_joined` until other members trigger another broadcast.
4. **Ticket Feed:** `ws/tickets/feed/` → `TicketListConsumer`. Events: `ticket_created/updated/assigned/closed/deleted`. Group: `ticket_feed_{tenant_id}`. Read-only (`receive_json` is a no-op). Client-side `ticket-feed.js` republishes events into `LiveBus` as `ticket.*`.
5. **VoIP:** `ws/voip/events/` → `CallEventConsumer`. Events: `call_ringing/answered/ended/hold`. Group: `voip_{tenant_id}`.
6. **Live (NEW):** `ws/live/` → `LiveEventConsumer`. Group: `live_tenant_{tenant_id}`. Read-only fan-out for newsfeed/CRM/contacts/comments/profile/membership events. Anon → close 4001; non-member → close 4003.

## Celery Tasks & Beat Schedule

### Queue Routing (`main/celery.py`)
```
apps.billing.tasks.*                              → kanzan_webhooks   (dormant — apps/billing/tasks.py does not exist)
apps.notifications.tasks.send_email_*             → kanzan_email
apps.notifications.tasks.send_notification_email  → kanzan_email
apps.inbound_email.tasks.*                        → kanzan_email
apps.tickets.tasks.send_ticket_*                  → kanzan_email
apps.voip.tasks.*                                 → kanzan_voip
*                                                 → kanzan_default
```
Default queue: `kanzan_default`. No explicit `include` list — relies on `app.autodiscover_tasks()`.

### Beat Schedule (9 tasks — `main/settings/base.py CELERY_BEAT_SCHEDULE`)
| Beat key | Task name (registered) | Schedule |
|----------|------------------------|----------|
| `check-sla-breaches` | `apps.tickets.tasks.check_sla_breaches` | 120s |
| `cleanup-old-notifications` | `apps.notifications.tasks.cleanup_old_notifications` | 86400s (daily) |
| `check-overdue-tickets` | `apps.tickets.tasks.check_overdue_tickets` | 900s (15m) |
| `calculate-lead-scores` | `apps.crm.tasks.calculate_lead_scores` | 86400s (daily) |
| `calculate-account-health-scores` | `apps.crm.tasks.calculate_account_health_scores` | 86400s (daily) |
| `kb-stale-alert` | `knowledge_base.alert_stale_articles` (registered-name override) | crontab daily 08:00 |
| `kb-gap-digest` | `knowledge_base.send_gap_digest` (registered-name override) | crontab Monday 09:00 |
| `cleanup-stale-calls` | `apps.voip.tasks.cleanup_stale_calls` | 3600s (hourly) |
| `fetch-inbound-emails` | `apps.inbound_email.tasks.fetch_inbound_emails_task` | 60s |

> Knowledge-base tasks register with `name="knowledge_base.alert_stale_articles"` / `name="knowledge_base.send_gap_digest"` (not the module path); both live in `apps/knowledge/tasks.py`.

> Celery Beat uses the **built-in shelve scheduler** (`celerybeat-schedule` file at repo root). `django-celery-beat` was removed — incompatible with Django 6.0.

> `apps.crm.tasks.check_overdue_reminders` and `apps.tickets.tasks.check_sla_breach_warnings` exist in code but are **NOT in the Beat schedule**.

### Task Inventory (23 tasks across 7 modules)
- **notifications**: `send_notification_email` (retries=3, default_retry_delay=60s, acks_late, kanzan_email), `cleanup_old_notifications` (batch 1000)
- **analytics**: `process_export_job` (retries=3; CSV/XLSX; openpyxl optional → CSV fallback; routes to `kanzan_default`)
- **inbound_email**: `fetch_inbound_emails_task`, `process_inbound_email_task` (retries=3, default_retry_delay=30s, acks_late, kanzan_email)
- **tickets**: `check_sla_breaches` (iterator chunk_size=200, dedup escalation rules, dedup via `sla_*_breached` flag), `check_overdue_tickets` (daily dedup), `send_ticket_reply_email_task`, `send_ticket_created_email_task`, `send_ticket_email_task`, `auto_close_ticket` (two-guard idempotency: status==resolved AND `auto_close_task_id==self.request.id`), `send_csat_survey_email`, `deliver_webhook_task` (exp backoff via `countdown=30*(2**retries)`), `check_sla_breach_warnings` (early warning), `propagate_sla_policy_change_task`
- **voip**: `process_call_recording`, `cleanup_stale_calls`, `sync_call_state` (queue `kanzan_voip`)
- **crm**: `check_overdue_reminders` (max_retries=1, acks_late, NOT in Beat — escalates to managers if overdue >24h), `calculate_lead_scores`, `calculate_account_health_scores`
- **knowledge**: `alert_stale_articles`, `send_gap_digest` (registered as `knowledge_base.*`)

## PM2 Processes — 5 prod / 4 dev

### `ecosystem.config.js` (prod, venv at `.venv/`)
| Name | Script | Purpose | Max mem |
|------|--------|---------|--------|
| `kanzan-django` | `.venv/bin/gunicorn main.asgi:application -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8001 --timeout 120 --graceful-timeout 30` | HTTP + WebSocket (ASGI) | 2GB |
| `kanzan-celery-worker` | `.venv/bin/celery -A main worker -Q kanzan_default,kanzan_email,kanzan_webhooks -c 4 --pool prefork --max-tasks-per-child=200 -n kanzan-worker@%h` | Background jobs | 2GB |
| `kanzan-celery-beat` | `.venv/bin/celery -A main beat -l info` | Periodic scheduler | 512MB |
| `kanzan-flower` | `.venv/bin/celery -A main flower --port=5556 --url_prefix=flower --basic_auth=$KANZAN_FLOWER_AUTH` | Monitoring dashboard | 512MB |
| `kanzan-smtp` | `.venv/bin/python manage.py run_smtp_server` | In-process SMTP (port 2525) | 512MB |

Common: `kill_timeout=8000ms` (15000ms for worker), `max_restarts=10`, `min_uptime="10s"`, `watch=false`, `merge_logs=true`.

> **The prod worker's `-Q` list is `kanzan_default,kanzan_email,kanzan_webhooks`.** The `kanzan_voip` queue is defined in `main/celery.py` routes, but VoIP tasks only run when `kanzan_voip` is added to `-Q` or a dedicated VoIP worker is started. `run_ari_listener` is **not in PM2** by default.

> **Makefile `stop` and `restart` targets omit `kanzan-smtp`** — manage it independently with `pm2 stop kanzan-smtp` etc.

### `ecosystem.dev.config.js` (dev, venv at `env/` symlinked to `.venv/`) — 4 processes
- `kanzan-django` runs `manage.py runserver 0.0.0.0:8001` (auto-reload on .py change), 2GB.
- `kanzan-celery-worker` `-c 2 --max-tasks-per-child=50`, **watch enabled** on `apps/*/tasks.py`, `apps/*/services.py`, `main/celery.py` with 2s delay (ignores `__pycache__/*.pyc/logs/env/static/media`), 1GB.
- `kanzan-celery-beat`, `kanzan-flower` (same as prod, lower memory caps).
- **No `kanzan-smtp` in dev.** Common: `max_restarts=50`, `min_uptime="3s"`.

## Frontend Architecture

### JavaScript (`static/js/`, 13 modules, ~4,160 LOC — vanilla, no React/Vue)
| Module | LOC | Role |
|--------|----:|------|
| **`live-bus.js` (NEW)** | 175 | Global pub/sub `window.LiveBus` (on/onMany/publish/debounce/rafBatch/isConnected/setChannelState + wildcards + BroadcastChannel cross-tab) |
| **`live-connection.js` (NEW)** | 206 | Single shared `wss?:/ws/live/` socket; 25s heartbeat / 8s pong; backoff 1s→30s+jitter, infinite retries; visibility hook for instant reconnect; `live.reconnected` on recovery |
| `api.js` | 90 | Central API client (CSRF from cookie, session credentials, JSON + multipart) |
| `app.js` (MODIFIED) | 843 | Global init: alerts, sidebar collapse, density, notification WS, Toast (uses var() colours), `Kanzan.formatDate/formatDateTime/timeAgo`, sidebar badge polling, **`initLiveStatusPill()`**, **`initSidebarUserLive()`**, `initSidebarBadges()` (still uses legacy `kanzan:notification` CustomEvent). **Working-tree change**: new-notification WS handler no longer calls `Toast.info` — instead `ringBell()` swings the bell + `showFlyout(data)` displays a bell-anchored peek-preview card (`#notifFlyout`) for 5s with an animated progress bar; `updateBadge(count, {bump:true})` re-triggers the `.is-bumping` scale animation on the badge |
| `ticket-feed.js` (MODIFIED) | 247 | WebSocket `ws/tickets/feed/`. Auto-connects via `data-ticket-feed` or URL match. Toasts + banner + row pulse. **Publishes into LiveBus** (lines 84–92): server `ticket_*` → `ticket.<verb>` + aggregated `ticket.event`. Banner click → `ticket.show_pending {count}` |
| `voip-softphone.js` | 710 | SIP.js 0.21.2 (CDN) + `CallEventConsumer`. Dial pad, DTMF, mute/hold/transfer/hangup, incoming-call modal |
| `notes-panel.js` | 238 | Quick notes CRUD (6 colors, pinning, localStorage) |
| `theme.js` | 77 | light/dark/system (default dark). Loaded SYNCHRONOUSLY in `<head>` to prevent FOUC. Persists `kanzan_theme` to localStorage. matchMedia listener |
| `agent-availability.js` | 227 | Status toggle + persistence. Uses `var(--status-info-dot)` etc. inline |
| `command-palette.js` | 337 | Cmd+K modal: static pages + dynamic search (200ms debounce on tickets/contacts) |
| `custom-select.js` | 371 | `KanzenSelect` global with portal rendering + searchable when >8 options |
| `rich-editor.js` | 191 | TipTap wrapper. Page-specific load (not in base.html) — used by ticket create/detail and KB article pages |
| `keyboard-shortcuts.js` | 318 | Global hotkeys: j/k navigate, Enter open, Esc deselect; a/s/x row actions; Ctrl+K palette; c new ticket; ? help; g d/t/c/b go-to. Injects runtime `<style>` using `var(--crm-primary)` etc. Disabled inside inputs |

### CSS
- **`static/css/custom-v15.css`** — **23,271 lines.** Live stylesheet, referenced from `base.html`. Design system "Crimson Black v9". Recent growth: API-keys Developer settings tab + reveal/regenerate/revoke modals + quickstart page (~+1.1k lines, fe0ad66) and the bell-anchored notification flyout + bell-ring/badge-bump animations (~+219 lines, uncommitted; respects `prefers-reduced-motion`).
- **`static/css/custom.css`** — 20,431 lines. Committed snapshot of the previous version; **NOT loaded** by base.html. Allowlisted in `scripts/check_theme.py` (so its legacy hex literals don't trip the regression guard).
- Foundation tokens added 2026-05-13 (in both `:root` and `[data-bs-theme="dark"]`): `--crm-text-on-primary/accent/dark`, `--crm-card-bg`, `--crm-input-bg{,-focus,-border}`, `--crm-scrollbar-{track,thumb,thumb-hover}`, `--crm-skeleton-{base,highlight}`, `--crm-overlay`.
- Components: stat cards with left accent, soft badges, kanban drag-and-drop, chat bubbles (now incl. pending-attachments tray), timeline dots, toast notifications (with exit animation), notes panel, knowledge base, calendar, softphone widget, audit log tabs/stats, command palette, quick notes, **"light island" auth pinning** (hardcoded values for autofill — explained inline; the only place hex literals are intentionally kept).
- Font: Inter (Google Fonts), 0.875rem fluid base; mobile collapses sidebar <992px.

### Theming Architecture (refactored 2026-05-13, refined in working tree)
Per-tenant runtime theming flows: **`TenantSettings.primary_color/accent_color`** → `apps/tenants/colors.py::derive_palette()` → `tenant_palette` context var (`apps/tenants/context_processors.py`) → inline `<style>` block in `templates/base.html` (lines 30–86) → CSS custom properties resolved by every rule body.

- **`apps/tenants/colors.py`** derives a **~21-value palette** from the tenant's primary + accent hex inputs (defaults `#C1121F` / `#E11D2D` — Crimson Black; note `TenantSettings` field defaults are `#6366F1`/`#F59E0B`, but the function-level fallback if the value is unset/malformed is Crimson Black): 50–900 scale, `primary_{hover,active,dark,light,subtle,ring,rgb}`, accent variants, and **`text_on_primary` / `text_on_accent`** picked by WCAG 2.x relative luminance (white vs near-black `#0B0B0B`, whichever wins ≥ 4.5:1). A `logger.warning` fires when the winning contrast is below WCAG AA so admins are alerted.
- **`base.html` palette block** (lines 30–86) emits **~32 CSS variables** (palette + Bootstrap overrides + semantic-red retheme + focus glows + `--crm-gradient`). Selector `:root, [data-bs-theme="light"], [data-bs-theme="dark"]` so the override wins over `custom-v15.css`'s defaults by source order. **Working-tree tweak:** `--crm-gradient` was changed from `primary→accent` to `primary→primary_dark` for less-saturated brand bars.
- **No hex literal in rule bodies.** Every brand red is routed through `var(--crm-primary*)`. Every white used as `color:` is `var(--crm-text-on-primary)`. Every neutral gray is property-aware (`color:` → `--crm-text-*`, `background:` → `--crm-surface*`, `border:` → `--crm-border*`). Status semantic colours route through the `--status-*` ramp.
- **JS color dicts** (`static/js/app.js` `NOTIF_TYPE_CONFIG`, `Toast._colors`; `keyboard-shortcuts.js`; `agent-availability.js`) use `'var(--crm-*)'` / `'var(--status-*)'` string literals. Browsers resolve `var()` at CSS-value time when assigned via `element.style.X = '<var()>'`.
- **`withAlpha(color, percent)` helper** (templates/pages/dashboard.html, tickets/list.html, contacts/list.html — NEW in working tree): when `color` is a hex, returns `hex+suffix`; when `color` is anything else (e.g. `var(--crm-primary)`), returns `color-mix(in srgb, <color> Y%, transparent)`. Replaces broken `color + '1A'` string concat patterns that silently dropped alpha on CSS-var inputs.
- **Chart.js compatibility** (templates/pages/dashboard.html): 2D canvas does not resolve CSS `var()`, so charts use `cssVar(name, fallback)` + `resolveColor()` helpers that read via `getComputedStyle` first.
- **Regression guard:** `scripts/check_theme.py` (run via `make theme-check`) scans static/templates for new off-token hex literals against a baseline (`scripts/.theme_baseline.json`). Strict mode (`make theme-check-strict`) fails on any hex outside the allowlist. Allowlist files: email templates (need inline hex for email clients), `templates/pages/settings/tenant.html` color picker, `apps/tenants/colors.py`, `static/js/theme.js`, `static/css/custom.css`. CSS comments + `:root`/`[data-bs-theme]` blocks + HTML `{% %}` tags + `<input type="color">` defaults + `data-*="hex"` attrs + entire `<script>` blocks are masked. Current baseline (`scripts/.theme_baseline.json`):

```json
{
  "static/css/custom-v15.css": 83,
  "templates/landing/landing_crm.html": 21,
  "templates/pages/auth/verify_email_sent.html": 3,
  "templates/pages/calendar.html": 2,
  "templates/pages/kanban/board.html": 1,
  "templates/pages/landing.html": 15,
  "templates/pages/reminders/list.html": 1,
  "templates/pages/tickets/list.html": 1
}
```

> Baseline for `custom-v15.css` went from 67 → 83 in the working tree to absorb the intentional hex retained for browser autofill workaround + "light island" auth pinning + the white brand mark.

- **Email templates excluded from sweep** — email clients strip `<style>` and require inline hex. Brand-coloured email tokens are left as literals; the right fix is to render them from `tenant_palette` at send time (separate refactor).

### Templates (51 files total)
- `templates/base.html` (254 lines) — layout + palette `<style>` + toast container + quick-notes panel + softphone include (conditional on `voip_enabled`) + DOMPurify v3.2.4 CDN + SIP.js 0.21.2 CDN (conditional) + Flatpickr 3-CDN-fallback global loader (jsdelivr → cdnjs → unpkg) + mobile detection IIFE (`is-mobile-html`/`is-mobile`/`is-mobile-sm` body classes based on touch + `screen.width<992/576`). Default theme: dark. Loads `static/css/custom-v15.css`. **Live-bus** always loaded; **live-connection** + agent-availability + notes + keyboard-shortcuts + ticket-feed conditional on `tenant and user.is_authenticated`.
- `templates/includes/` (5 files) — `navbar.html` (with `#liveStatusPill` + the bell-anchored `#notifFlyout` peek-preview card added in working tree), `sidebar.html` (with `data-current-user-id`), `softphone.html`, `messages.html`, `kb_sidebar_widget.html` (**orphan** — no includer; check before removing).
- `templates/pages/` — **14 subfolders** + 7 root files. Folders: agents/, analytics/, audit_log/, auth/, billing/, contacts/, emails/, **groups/**, inbound_email/, kanban/, knowledge/, messaging/, reminders/, settings/, tickets/, users/, voip/. Root: 403.html, calendar.html, dashboard.html, landing.html, login.html, profile.html, register.html.
- `templates/landing/landing_crm.html` — standalone marketing page (doesn't extend `base.html`; tolerates landing-specific hex literals in the theme baseline).
- Email templates (12 files / 6 pairs): `auth/email/verify_email.{html,txt}`, `tickets/email/{ticket_created,reply_notification,csat_survey}.{html,txt}`, `notifications/email/notification.{html,txt}`, `knowledge/email/article_rejected.{html,txt}`.

### Context Processor (`apps/tenants/context_processors.py`)
Injects into every template: `tenant`, `membership`, `user_role` (= `effective_role`), `is_admin`, `is_admin_or_manager`, `is_agent_or_above`, **`voip_enabled`** (queries `VoIPSettings.is_active`; controls softphone inclusion), **`tenant_palette`** (~21-key dict from `derive_palette`), `BASE_URL`. Caches membership on `request._cached_tenant_membership` to avoid double-fetch with DRF permission classes.

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
10. **TenantMiddleware** (tenant resolution + async-safe context)
11. **SubscriptionMiddleware** (billing enforcement — returns HTTP 402 when neither `is_active` nor `in_grace_period`)
12. **RateLimitHeadersMiddleware** (`apps.api_keys.middleware` — emits `X-RateLimit-Limit/Remaining/Reset` on API-key requests; reads from `request._kanzan_throttle_info` stashed by `APIKeyRateThrottle`)
13. MessageMiddleware
14. XFrameOptionsMiddleware

## Third-Party Integrations (selected)

| Integration | Version | Purpose | Config |
|-------------|---------|---------|--------|
| Django | 6.0.2 | Framework | — |
| DRF | ≥3.16,<4 | REST API | — |
| Channels | ≥4.2,<5 | WebSocket real-time | Redis db5 |
| channels-redis | ≥4.2,<5 | Channel layer | prefix `kanzan:channels` |
| Celery | ≥5.4,<6 | Background tasks | Redis db4 broker, django-db results, built-in shelve beat |
| django-celery-results | ≥2.5,<3 | Celery result store | django-db |
| django-redis | ≥5.4,<6 | Cache + sessions | Redis db3, prefix `kanzan` |
| psycopg | ≥3.2,<4 | PostgreSQL driver (binary) | DATABASE_URL |
| Stripe | ≥11,<12 | Payments, subscriptions | STRIPE_* envs |
| django-allauth | ≥65,<66 | OAuth2 SSO | `ACCOUNT_LOGIN_METHODS={"email"}` |
| SimpleJWT | ≥5.4,<6 | JWT auth | 15m access, 7d refresh, rotate+blacklist, HS256 |
| DRF-Spectacular | ≥0.28,<0.29 | OpenAPI 3.0 docs | `/api/docs/` |
| django-filter | ≥24.3,<25 | API filtering | DjangoFilterBackend |
| django-cors-headers | ≥4.6,<5 | CORS | — |
| django-environ | ≥0.12,<1 | Env config | reads `.env` |
| WhiteNoise | ≥6.8,<7 | Static serving | CompressedManifestStaticFilesStorage in prod |
| python-magic | ≥0.4,<0.5 | MIME detection | Avatar/logo/attachment uploads |
| Pillow | ≥11,<12 | Image processing | Avatars, logos |
| mammoth | ≥1.12,<2 | `.docx` → HTML | Knowledge base imports |
| openpyxl | ≥3.1,<4 | Excel export | analytics ExportJob (optional — CSV fallback) |
| aiosmtpd | ≥1.4,<2 | In-process SMTP server | Inbound email |
| httpx | ≥0.27,<1 | Async HTTP | Asterisk ARI client |
| websockets | ≥12,<14 | WebSocket client | ARI Stasis events |
| SIP.js | 0.21.2 (CDN) | Browser SIP/WebRTC | VoIP softphone |
| Bootstrap | 5.3.3 (CDN) | CSS framework | — |
| Tabler Icons | 3.31.0 (CDN) | Icon webfont | — |
| DOMPurify | 3.2.4 (CDN) | XSS sanitization | Ticket detail, KB articles |
| Flatpickr | latest (3-CDN fallback) | Date pickers | base.html loader |
| Chart.js | 4 (page-specific CDN) | Dashboard trends | — |
| SortableJS | 1.15.2 (page-specific CDN) | Kanban DnD | — |
| Jazzmin | ≥3.0,<4 | Admin theme | Custom sidebar + model icons |
| daphne | ≥4.2,<5 | ASGI server | Listed in INSTALLED_APPS |
| gunicorn | ≥25,<26 | WSGI/ASGI server | Production |
| uvicorn[standard] | ≥0.40,<1 | ASGI worker | Production |
| Flower | ≥2.0,<3 | Celery monitoring | Port 5556 |

**Dev tools:** pytest ≥8.3, pytest-django ≥4.9, pytest-asyncio ≥0.24, pytest-cov ≥6, factory-boy ≥3.3, faker ≥33, ruff ≥0.8, django-debug-toolbar ≥4.4, django-extensions ≥3.2, ipython ≥8.31. **`requirements/prod.txt` has zero extras — just `-r base.txt`.**

## Billing Plans

| Plan | Users | Contacts | Tickets/mo | Storage | API | SSO | SLA | VoIP | Call Recording |
|------|-------|----------|-----------|---------|-----|-----|-----|------|----------------|
| Free | 3 | 500 | 100 | 1GB | No | No | No | No | No |
| Pro | 25 | 10K | 5K | 25GB | Yes | No | Yes | Yes | Yes |
| Enterprise | Unlimited | Unlimited | Unlimited | Unlimited | Yes | Yes | Yes | Yes | Yes |

Plan also has: `has_realtime`, `has_custom_roles`, `max_custom_fields`, `max_calls_per_month`, `audit_retention_days` (NULL = unlimited).

## Management Commands

```bash
# Tenancy
python manage.py provision_tenant --name "Acme" --slug acme [--domain crm.acme.com]

# Seeding
python manage.py seed_plans                                    # Free/Pro/Enterprise (idempotent)
python manage.py setup_queues --tenant-slug demo               # 4 default queues
python manage.py setup_ticket_statuses --tenant-slug demo      # 5 default statuses
python manage.py backfill_sla_audit [--tenant-slug] [--dry-run]   # baseline SLA audit for in-flight tickets

# Long-running daemons
python manage.py run_smtp_server                               # kanzan-smtp PM2 process
python manage.py run_ari_listener                              # VoIP Stasis event loop (NOT in PM2 — start separately if VoIP is live)
```

## Environment Variables

### In `.env.example` (16 keys)
`DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DATABASE_URL`, `REDIS_URL`, `BASE_DOMAIN`, `BASE_SCHEME`, `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`, `JWT_SECRET_KEY`, `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `KANZAN_FLOWER_AUTH`.

### Read by `base.py` but NOT in `.env.example` (18+ keys)
- **Base:** `BASE_PORT`
- **IMAP:** `IMAP_HOST`, `IMAP_PORT`, `IMAP_USER`, `IMAP_PASSWORD`, `IMAP_MAILBOX`, `IMAP_USE_SSL`, `IMAP_DEFAULT_TENANT_SLUG`
- **SMTP server:** `SMTP_SERVER_HOST`, `SMTP_SERVER_PORT`, `SMTP_SERVER_HOSTNAME`, `SMTP_SERVER_REQUIRE_AUTH`, `SMTP_SERVER_AUTH_USERS` (JSON dict), `SMTP_SERVER_TLS_CERT_FILE`, `SMTP_SERVER_TLS_KEY_FILE`
- **Inbound webhook (unused today):** `INBOUND_EMAIL_WEBHOOK_SECRET`
- **Email:** `EMAIL_BACKEND`, `DEFAULT_FROM_EMAIL`, `EMAIL_TIMEOUT`, `EMAIL_USE_SSL`

`main/settings/__init__.py` loads `base.py` then conditionally loads `dev.py` (when `DJANGO_DEBUG=True`) or `prod.py` (with try/except — silently ignores ImportError). `pytest.ini` sets `DJANGO_SETTINGS_MODULE=main.settings`.

## Testing

### Infrastructure
- **Framework:** pytest + pytest-django.
- **Module counts:** **54 root-level** `tests/test_*.py` + **7 app-level** (`apps/knowledge/tests/test_kb_gap_fill.py`, `apps/tickets/tests/test_{creation,escalation}.py`, plus 4 new `apps/api_keys/tests/test_{authentication,documentation,throttling,viewset}.py`) = **61 total** (~16,242 LOC in root; ~1,150 LOC for `apps/api_keys/tests/`; 43 test functions in api_keys split 15 / 19 / 5 / 4).
- **Config:** `pytest.ini` — `DJANGO_SETTINGS_MODULE=main.settings`, `pythonpath=.`. **No `asyncio_mode`** (pytest-asyncio defaults to `strict` — explicit decorators required). No `pyproject.toml`.
- **Fixtures (`conftest.py`):** **16 factories + 20 fixtures (3 autouse:** `celery_eager`, `free_plan`, `clear_tenant_context`**).** `viewer_role`/`viewer_user`/`viewer_client` rely on the Viewer role being seeded by `apps/tenants/signals.py` on tenant creation.
- **Legacy base** (`tests/base.py`): `TenantTestCase` + `KanzenBaseTestCase` providing `tenant_a`/`tenant_b`, free + pro plans, all 4 roles per tenant, ticket statuses + SLA policy + helpers.
- **Celery:** Eager mode (autouse fixture `celery_eager`).

### Working-tree test edits
- `tests/test_phase3_resolution.py` — one test was flipped: `test_waiting_to_resolved_blocked` (expected ValidationError) → `test_waiting_to_resolved_allowed` (Waiting → Resolved is now legal; SLA pause is cleared). Aligns with the `services.py` `ALLOWED_TRANSITIONS["waiting"]` widening.
- `apps/api_keys/tests/test_authentication.py` — removed unused imports (`Role`, `APIKey`).
- `apps/api_keys/tests/test_viewset.py::test_email_task_queued_on_create` — now wraps the POST in `django_capture_on_commit_callbacks(execute=True)` because `transaction.on_commit` callbacks are otherwise discarded by `pytest.mark.django_db`'s atomic-rollback teardown. This is the canonical pattern for exercising post-commit Celery dispatch under pytest-django.

## Recent Migration Highlights (verify against `git log`)

| App | Latest | What it adds |
|-----|--------|--------------|
| accounts | **0010_user_is_service_account** | `User.is_service_account` BooleanField (db-indexed) — marks the hidden synthetic users minted by `apps.api_keys` |
| accounts | 0009_usergroup | `UserGroup` model (tenant-scoped, M2M members) — used by `Article.allowed_groups` |
| accounts | 0008_add_temporary_permissions | `TenantMembership.temporary_permissions` M2M (per-grant permission subset; intersect with `temporary_role.permissions`) |
| agents | **0006_customagentstatus_…_custom_status** | `CustomAgentStatus` model + `AgentAvailability.custom_status` FK |
| analytics | 0003_calendarevent_color_calendarevent_end_date_and_more | Calendar event color + end_date |
| **api_keys** | **0001_initial** | `APIKey` model (TenantScoped) — service-account credentials with prefix lookup + SHA-512 hash |
| billing | 0002_plan_has_call_recording_plan_has_voip_and_more | VoIP feature flags on Plan |
| comments | **0009_alter_activitylog_action** | ActivityLog action choices grow to **26** (adds api_key_created/regenerated/revoked) |
| comments | 0008_alter_activitylog_action | ActivityLog action choices were at 23 (added reminder lifecycle + outbound_call_*) |
| contacts | 0005_widen_phone_field | Wider phone field |
| crm | 0004_reminder_m2m_contacts_tickets | **Reminder.contact/ticket FK → contacts/tickets ManyToMany** with data-preserving copy |
| inbound_email | 0008_…_imappollstate | IMAPPollState model (uid_validity + last_uid watermark) |
| kanban | 0004_board_is_personal | `Board.is_personal` boolean |
| knowledge | 0005_article_allowed_groups | `Article.allowed_groups` M2M to UserGroup (visibility scoping) |
| messaging | 0002_conversation_source_group | `Conversation.source_group` FK |
| newsfeed | 0002_reactions_reads_enhancements | Reactions + read receipts |
| notifications | 0004_rename_recall_to_reminder | Rename Recall→Reminder |
| tenants | 0008_…_auto_assign_inbound_email_tickets | Auto-assign toggle for inbound email |
| tickets | 0026_alter_ticketactivity_event | TicketActivity event choices grow to 27 (adds inbound_call/inbound_call_completed) |
| voip | 0002_voipsettings_asterisk_use_ssl_and_more | Asterisk SSL config |

## Performance Optimizations

- **Analytics closed-status cache** — per-request `_closed_status_cache` in `DashboardView` avoids repeated DB lookups across `get_ticket_stats`/`get_agent_performance`/`get_due_today`/`get_overdue_tickets`.
- **Kanban N+1 fix** — `BoardDetailSerializer.get_columns` batch-fetches GenericFK content objects grouped by content_type; Tickets pre-select `status` and `assignee`.
- **Kanban populate** — `populate_board_from_tickets` uses subquery `.exclude()` instead of loading all ticket IDs.
- **Comment attachment prefetching** — ticket detail batch-fetches and sets `_prefetched_attachments`. Same pattern now used by `MessageSerializer.attachments`.
- **Contact group bulk add** — set-based batch (one query) instead of per-contact `exists()` check.
- **Company `contact_count` annotation** — at DB level; `ContactGroupSerializer` caps contacts at 50.
- **Message reply count** — annotated via `Count("replies")`.
- **SLA breach iteration** — `iterator(chunk_size=200)` to bound memory.
- **First-response race** — atomic UPDATE + WHERE filter (no `save()`).
- **Bulk ops** — `bulk_update_tickets` handles failures independently per operation.
- **Lead/health scoring** — pre-fetches signal sets, iterates contacts via `.iterator(chunk_size={500,200})`, bulk-updates by score bucket.
- **Reminder overdue task** — `Reminder.unscoped + iterator(chunk_size=200)`, 1-per-day dedup via Notification filter on `data__reminder_id`.

## Security Hardening

- `IsTenantMember` applied to AttachmentViewSet, BoardViewSet, ColumnViewSet, CardPositionViewSet, ContactGroupViewSet, ConversationViewSet, MessageViewSet, NotificationViewSet, NotificationPreferenceViewSet, QuickNoteViewSet, ReminderViewSet, InboxViewSet — blocks cross-tenant JWT access.
- **`LiveEventConsumer`** explicitly verifies tenant membership before joining the channel-layer group — JWT-only clients that know a tenant slug cannot eavesdrop. **Caveat:** `Comment.is_internal` events are broadcast on the tenant-wide live channel; clients are expected to filter. A non-agent user with an open live WS receives the payloads. Filtering happens only in the UI — a latent information-leak vector. The signal file's docstring acknowledges this and proposes per-role groups as a follow-up.
- ChatConsumer rate limits (10KB msg, 5/sec, 2s typing cooldown) and tenant-from-scope verification. TicketPresenceConsumer, TicketListConsumer, CallEventConsumer all verify tenant membership.
- **Webhook `secret`** write-only in serializer responses; HMAC SHA-256 signing; auto-disable at 10 consecutive failures.
- **XSS prevention** — ticket detail uses `textContent` for description; knowledge base sanitizes mammoth output (strips `<script>` and `on*` handlers); PDF/image preview URLs HTML-escaped; DOMPurify available globally for client-side sanitization.
- **Auth throttling** — `AuthViewSet.throttle_scope = "auth"` (10/min). **Only viewset in repo with a throttle scope set.** `APIKeyRateThrottle` (1000/hour) is opt-in-free — auto-engages on any `request.auth = APIKey` request.
- **Tenant queryset scoping** — `TenantViewSet` filters by user's memberships (superusers see all).
- **File-upload MIME** — python-magic with content-type fallback (avatars, logos, attachments); 2MB avatar cap; 25MB general cap (`FILE_UPLOAD_MAX_MEMORY_SIZE`/`DATA_UPLOAD_MAX_MEMORY_SIZE`).
- **SSO fields** — `sso_client_id/authority_url/scopes/secret` write-only in TenantSettings serializer.
- **Attachment cross-tenant** — `AttachmentUploadSerializer.validate()` ensures target object belongs to current tenant.
- **Stripe subscription tenant tracking** — `subscription_data.metadata.tenant_id` on checkout + webhook handler resolves tenant from subscription metadata.
- **Password validation** — full Django `validate_password()` (complexity + common-password list).
- **Global logout via `auth_version`** — `SessionVersionMiddleware` invalidates sessions when user.auth_version is bumped.
- **InboundEmail immutability** — `linked_at/by` and `actioned_at/by` raise `ValidationError` if changed once set.
- **IMAP "never backfill"** — poll aborts cleanly rather than ingesting historical mail when UIDVALIDITY/UIDNEXT can't be parsed.
- **Superuser-only admin** — `main/admin.py::SuperuserOnlyAdminSite` replaces the default site; non-superusers 403 on `/admin/`.

## Key Implementation Details

- **Live broadcasts are best-effort + transactional.** `broadcast_live_event` defers via `transaction.on_commit` (so a rolled-back save never leaks an event) and swallows exceptions in `_send` (so a Redis blip never breaks user writes).
- **`effective_role` everywhere.** Wherever you would normally compare `membership.role.hierarchy_level`, compare `membership.effective_role.hierarchy_level` instead so temporary role grants are honoured. The context processor and `HasTenantPermission` already do this; new code must follow.
- **`has_effective_permission` honours `temporary_permissions` intersection** — use it instead of poking at `effective_role.permissions` directly.
- **Ticket number per-tenant sequencing:** dedicated `TicketCounter` model (unscoped, SELECT FOR UPDATE) — replaces older max-number query approach.
- **Signal dedup flag:** set `instance._skip_signal_logging = True` before save; use `serializer.instance` in `perform_update` so the flag reaches the signal. 2-sec window.
- **Session cookie domain:** Dev host-only (per-origin); prod also host-only due to Chrome's strict `.localhost` policy. Cross-tenant handoffs use signed tokens.
- **CSRF trusted origins:** Dev `http://localhost:8001` + `http://*.localhost:8001`; prod `https://*.{BASE_DOMAIN}`.
- **File upload paths:** `tenants/{tenant_id}/attachments/YYYY/MM/{filename}` (attachments); `tenants/{tenant_id}/recordings/YYYY/MM/{uuid}.{ext}` (VoIP); `inbound_emails/{pk}/{filename}` (inbound).
- **InboundEmail tenant resolution:** model extends `TimestampedModel` (NOT TenantScopedModel) — `tenant` FK nullable, set post-parse. Subject sanitized (strip `\r`/`\n`).
- **CannedResponse ownership:** only creator or Manager+ can edit/delete shared ones.
- **SavedView default race:** `set_default()` uses `transaction.atomic() + select_for_update()`.
- **SLA breach flag persistence:** `response_breached`/`resolution_breached` saved to DB before firing notifications (dedup across retries).
- **Ticket soft delete:** DELETE sets `is_deleted=True`, `deleted_at`, `deleted_by`; default queryset excludes; `?include_deleted=true` shows them; POST `restore/` reverses.
- **Ticket watchers:** duplicates return 409; `reason` (manual/mentioned/commented/cc'd); `is_muted` suppresses notifications; list annotates `watcher_count`.
- **Time tracking:** 1–1440 minute range, billable flag, optional started_at/ended_at, users delete only own entries. `time-summary/` aggregates.
- **Ticket templates:** `usage_count` via POST `use/`; active-only in list.
- **Circular ticket link prevention:** `_creates_circular_dependency` BFS — e.g. A→B→A blocked on blocks/blocked_by.
- **SLA filters:** `?sla_approaching=true` (≤30m), `?has_sla=true/false`, `?sla_response_breached`, `?sla_resolution_breached`.
- **Reminder model:** `subject`/`notes`/`scheduled_at`/`completed_at`/`cancelled_at`/`priority`/`assigned_to`/`created_by` plus M2M `contacts` and `tickets`. `status` is a derived property. Has `unscoped` manager.
- **Pipeline default race:** `is_default=True` setter atomically demotes prior default.
- **Macro application:** renders `{{ticket.*}}`, `{{contact.*}}`, `{{agent.*}}`, `{{ticket.queue}}` variables + executes actions atomically.
- **BusinessHours schedule default:** Mon–Fri 09:00–17:00, Sat–Sun off.
- **Reminder renamed from Recall** in crm migration 0003 (data-preserving). Migration 0004 then converted single FKs to M2Ms.
- **Temporary role override:** `TenantMembership.effective_role` returns `temporary_role` when `temporary_role_expires_at > now()`, else `role`. Curated `temporary_permissions` M2M intersects with `temporary_role.permissions` to scope the grant.
- **Knowledge `Article.allowed_groups`** (M2M UserGroup, migration 0005) — restricts visibility to members of named groups; empty M2M = visible to all in tenant.
- **Kanban `Board.is_personal`** (migration 0004) — personal boards are private to the creator.

## Common Pitfalls & Fixes Applied

1. `TenantSettings` dual-PK issue — removed `primary_key=True` from OneToOneField.
2. Allauth config must be a set: `ACCOUNT_LOGIN_METHODS = {"email"}`.
3. All apps need `migrations/__init__.py`.
4. DRF upgraded 3.15.2 → 3.16.1 (Django 6.0 compatibility).
5. `base.html` needs `user.is_authenticated` check (AnonymousUser has no `.email`).
6. Role creation signal must include `hierarchy_level` (10/20/30/**40 for Viewer**).
7. Ticket stats JS reads `data.ticket_stats` (not `data.ticket_summary`).
8. Flower package added to requirements/base.txt.
9. **Viewer IS seeded by default** (`apps/tenants/signals.py:53`). Older docs that said otherwise are wrong.
10. `swagger_fake_view` check in `get_queryset()` to survive OpenAPI schema generation.
11. Use `get_user_model()` (not direct import) in async consumers.
12. Test fixtures: `UserFactory` uses `_after_postgeneration` with `skip_postgeneration_save = True`.
13. Test base: `current_period_start/end` must be tz-aware datetimes.
14. **`django-celery-beat` removed** — not Django 6 compatible; Celery built-in shelve scheduler used.
15. **VoIP queue note** — `kanzan_voip` is defined in `celery.py` routes but the default worker's `-Q` list does not include it; add it or start a dedicated VoIP worker before enabling VoIP tasks.
16. **PM2 process count** — 5 prod processes (django, celery-worker, celery-beat, flower, smtp). Makefile `stop`/`restart` omit `kanzan-smtp` — manage that one separately.
17. **9 Beat tasks** including `fetch-inbound-emails` (60s), `calculate-lead-scores` (daily), `calculate-account-health-scores` (daily), `cleanup-stale-calls` (hourly), `kb-stale-alert`/`kb-gap-digest`. `check_overdue_reminders` and `check_sla_breach_warnings` exist in code but are NOT scheduled.
18. **CSS versioning** — `static/css/custom-v15.css` is the live file referenced by `base.html` (**23,271 lines**). `custom.css` (20,431 lines) is a committed snapshot, not loaded, allowlisted in theme check.
19. **IMAP "never backfill" safety** — UIDVALIDITY/UIDNEXT must be parseable to bare integers; the poller aborts (returns 0) on first run rather than match `1:*`.
20. **Tenant primary_color / accent_color override is supported** in `templates/base.html` (lines 30–86). `TenantSettings` defaults `#6366F1`/`#F59E0B`; `colors.py::derive_palette` falls back to Crimson Black `#C1121F`/`#E11D2D` if hex parsing fails. Validate any color string server-side if accepting user input.
21. **Reminder M2M migration** — `contacts` and `tickets` are M2Ms (crm migration 0004), not single FKs. Older code referencing `reminder.contact` or `reminder.ticket` will break.
22. **Knowledge-base task names** — `alert_stale_articles` and `send_gap_digest` register under the `knowledge_base.*` namespace.
23. **TicketActivity events** — 27 choices total after migration 0026.
24. **ActivityLog actions** — **26 choices** total after comments migration 0009 (api_key_created/regenerated/revoked added on top of the 23 from migration 0008).
25. **Temporary role overrides** (accounts migration 0007) — use `effective_role` (not `role`) wherever the active role matters.
26. **API router include count is 22** — `main/urls.py` has 22 `/api/v1/*/` `path()` lines after `api-keys/` was added, but `inbound-email/` is dual-mounted at `emails/` with `namespace="emails_api"`, so there are 21 *unique* URLConfs.
27. **Frontend URL count is 34** — `apps/tenants/frontend_urls.py` now includes `/api/quickstart/` (developer guide, any member can view) on top of `/groups/`, `/calls/`, `/inbound-email/`, and the older 28-page set.
28. **Total model class count is 83**, across 20 apps with `models.py` (api_keys adds `APIKey`; `nav` is URL-only — no models.py, no AppConfig, no migrations).
29. **No new hex colour literals in CSS/JS/template rule bodies** (post-theming refactor, 2026-05-13). Token blocks (`:root`, `[data-bs-theme]`) are the ONLY place where new hex values are permitted. `make theme-check` enforces this against the baseline in `scripts/.theme_baseline.json`.
30. **Use `var(--crm-text-on-primary)` (not `#FFFFFF`) for text/icon foregrounds on tenant-themed surfaces**. The value is computed per-tenant by WCAG luminance — falls to near-black automatically for light tenant primaries.
31. **JS color strings use var() too** — `element.style.backgroundColor = 'var(--crm-primary)'` works because the browser resolves var() at CSS-value time. Hex literals in JS would silently break tenant theming.
32. **Hex-alpha concat is forbidden** — `'#abc' + '1A'` breaks when the input becomes a `var(--crm-*)`. Use `withAlpha(color, percent)` helper (added in working tree) which falls back to `color-mix(in srgb, <color> Y%, transparent)`.
33. **Chart.js can't resolve `var()`** — dashboard charts use `cssVar(name, fallback)` + `resolveColor()` (read via `getComputedStyle`) before painting to the canvas.
34. **Live broadcast layer is uncommitted in working tree** (2026-05-14/15). 4 brand-new `signals.py` files (comments, contacts, crm, newsfeed), one new consumer (`apps/tenants/{live,consumers,routing}.py`), two new JS modules (`live-bus.js`, `live-connection.js`), and `app.js`/`ticket-feed.js` updates. Sign off carefully before committing — adds a 6th WebSocket consumer and broadcasts on every save/delete in 5 apps.
35. **Comment broadcasts ignore `is_internal`** — internal comments are emitted on the tenant-wide live channel. Clients must filter; non-agent UI receives them. Known limitation flagged for follow-up (per-role groups).
36. **TicketPresenceConsumer has a documented-but-unimplemented `presence_list`** — newly joined clients only see their own `agent_joined` until other members trigger another broadcast. Latent bug for the presence UI on first paint.
37. **`UserGroup`** (accounts migration 0009) — new tenant-scoped model; M2M members; surfaces in `apps/tenants/frontend_urls.py` `/groups/` page and `Article.allowed_groups`. **Not registered in admin.**
38. **`CustomAgentStatus`** (agents migration 0006) — tenants can now define custom status labels (e.g. "In Meeting", "Lunch") with a slug + color; `AgentAvailability.custom_status` FK lets agents pick one. **Not registered in admin.**
39. **Messaging attachments (uncommitted)** — `MessageCreateSerializer.body` is now `allow_blank=True, required=False, default=""`. Attachment-only messages valid serializer-side; the frontend must enforce "neither body nor attachments → reject". New `POST .../messages/{id}/broadcast/` re-emits over chat group after attachments are linked (author-only). `_broadcast_message` now a `@classmethod`.
40. **Status transition relaxation (uncommitted)** — `apps/tickets/services.py::ALLOWED_TRANSITIONS["waiting"]` now allows `resolved` and `closed` (was `open`, `in-progress` only). Test `test_phase3_resolution.py` flipped to expect success on Waiting → Resolved.
41. **`apps/billing/tasks.*` queue route is dormant** — `apps/billing/tasks.py` doesn't exist. The `kanzan_webhooks` route exists in `main/celery.py` for future use.
42. **Notification is NOT polymorphic** — its `data` JSONField holds linkage info; there is no GenericFK. Older docs that listed Notification under "Polymorphic Models" are wrong. The 5 truly polymorphic models are: `Attachment`, `Comment`, `ActivityLog`, `CustomFieldValue`, `CardPosition`.
43. **No CI/CD** — no `.github/` directory, no Dockerfile, no docker-compose, no GitLab CI. Pre-commit gate is `make check` (lint + migrate-check + test). Deployment is PM2 on a single host.
44. **`BASE_SCHEME` IS in `.env.example`** at line 7 (older note saying otherwise was wrong).
45. **`main/admin.py` is real** — `SuperuserOnlyAdminSite` + `TenantFilteredAdmin` mixin. 9 files in `main/`, not 8.
46. **API keys are tenant-scoped service-account credentials** (`apps/api_keys/`, fe0ad66). Cleartext format `kz_live_<tenant_slug>_<secret>`; only `prefix` (indexed) + SHA-512 hash are persisted; the secret is shown exactly once at mint/regenerate time and is unrecoverable afterward. Auth class returns `None` (not 401) when the `Authorization` header is missing or uses a different scheme, so JWT/Session still get a chance. **Hidden synthetic users** back each key — they have `is_service_account=True` and must be filtered out of user-facing staff lists. Permission inheritance is via the key's `role` FK (drives `HasTenantPermission` as if a real user). Mint/regenerate/revoke all write an `ActivityLog` row (`api_key_created/regenerated/revoked`) and the create flow queues `send_api_key_created_email_task` via `transaction.on_commit`.
47. **`APIKeyRateThrottle` is `SimpleRateThrottle`-based, not `ScopedRateThrottle`** (uncommitted working-tree change to `apps/api_keys/throttling.py`). It engages on every API-key-authenticated request without per-viewset `throttle_scope` opt-in; non-API-key auth (`request.auth` not an `APIKey`) returns `None` from `get_cache_key` and is skipped entirely. The throttle stashes `(limit, remaining, reset_epoch)` on **both** `request._kanzan_throttle_info` and `request._request._kanzan_throttle_info` so the response-path Django middleware (which sees the underlying `HttpRequest`) can read it regardless of which reference it holds.
48. **`RateLimitHeadersMiddleware`** (`apps.api_keys.middleware`) — slot 12 in the middleware stack, between `SubscriptionMiddleware` and `MessageMiddleware`. Pure read-side; only emits `X-RateLimit-*` headers when the throttle stashed info on the request. Cost is essentially zero for non-API-key traffic.
49. **`drf-spectacular` OpenAPI extension for API keys** is registered by `apps/api_keys/apps.py::ready()` importing `apps.api_keys.extensions` (uncommitted working-tree change — the merge commit had the extension file but missed wiring it). Swagger UI's "Authorize" dialog gains an `ApiKeyAuth` option alongside the existing JWT bearer.
50. **Notification UX (uncommitted working-tree change)** — new notifications no longer fire a generic `Toast.info`. Instead the bell icon (`#notifDropdown`) gets `.is-ringing` for ~950ms (CSS swing + radial halo) and the new bell-anchored card (`#notifFlyout`) slides in below the bell with a 5s auto-fade and animated progress bar. The unread-count badge gains `.is-bumping` for a one-shot scale animation. All animations respect `@media (prefers-reduced-motion: reduce)`. Files: `templates/includes/navbar.html` (DOM), `static/js/app.js::initNotifications` (`ringBell()`, `showFlyout(data)`, `hideFlyout()`), `static/css/custom-v15.css` (~+219 lines, `.notif-bell-btn`, `.notif-flyout*`, `.notification-badge.is-bumping`, `@keyframes notif-bell-ring/halo/badge-bump/flyout-timer`).

## Documentation
- `/CLAUDE.md` (this file) — day-to-day source of truth, kept current with refactors.
- `/docs/README.md` — index for the docs folder.
- `/docs/architecture.md` — long-form architecture doc (Version 1.0, dated 2026-02-06; **stale** — predates auto-assign, IMAPPollState, temporary-role, Reminder M2M, ActivityLog action expansion, `kanzan-smtp` PM2 process, `fetch-inbound-emails` Beat task, UserGroup, CustomAgentStatus, **and the live broadcast layer**). Use as broad design rationale; trust this CLAUDE.md and the verified `/docs/reference/` files for current shape.
- `/docs/reference/codebase-inventory.md` — verified per-app model/migration/task/signal inventory (last regenerated 2026-05-11; predates `apps/{comments,contacts,crm,newsfeed}/signals.py` and the live layer).
- `/docs/reference/api-surface.md` — every REST endpoint, custom action, WebSocket consumer, permission class.
- `/docs/reference/frontend-surface.md` — every template, JS module, CSS file, frontend URL.
- `/docs/reference/infra-surface.md` — settings, middleware, ASGI, Celery, PM2, requirements, env, scripts, tests.
- `/scripts/check_theme.py` — regression guard for theme leakage. Run via `make theme-check` (delta vs baseline) / `make theme-check-strict` (zero-tolerance) / `make theme-baseline` (refresh after intentional changes).
- `README.md` — minimal stub (1 line: `# Kanzen`); rely on the documents above for context.
