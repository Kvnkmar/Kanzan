# Kanzen — Project Intelligence

> Last refreshed: **2026-06-03** — verified against branch `main` @ `ae5aaac` ("ui(css): snap stray font-weight 450/550 to --crm-weight tokens") via fresh deep-dive (5 parallel agents). Working tree carries **24 modified files, +8,123 / −4,795 lines** across three workstreams that have continued to evolve past the 2026-05-26 snapshot. **(1) Inbox Hub Phase 1 MVP is now full-stack** — what the prior doc described as "Phase 0 + 1A backend only" now includes a complete frontend: `templates/pages/inbox_hub/list.html` (281 LOC, 3-column split workspace) + `static/js/inbox-hub.js` (666 LOC vanilla IIFE controller) + ~535 LOC of `.ih-*` CSS at `static/css/custom-v15.css:24088+`, mounted at `/inbox-hub/` (frontend) and `/api/v1/inbox-hub/hub-emails/` (REST). The sidebar gains an "Inbox Hub" link with `#sidebarBadgeInboxHub` badge, `BadgeCountView._inbox_hub_count` is wired (was previously "out of scope for Phase 1A"), `app.js::initSidebarBadges` subscribes to 7 `hub_email.*` LiveBus events for debounced badge refetch, and `page_back_button.html` excludes `/inbox-hub/`. Backend stub state otherwise unchanged from the prior doc: `_post_park_hooks` is still empty, no `routing.py`/`assignment.py`/`signals.py`/`tasks.py`, no assign/reassign/escalate API actions, no HUB_EMAIL_* Notification creators. **(2) Reminders v2 split-pane workspace** — `templates/pages/reminders/list.html` is now **3,263 LOC** (working tree shows +3,685 inserts / −959 deletes; HEAD was 1,496). Replaces the old modal+table flow with a split-pane workspace (state.mode machine `{empty,view,form}`, workload progress banner, 5 grouped buckets overdue/today/upcoming/done/cancelled, quick-filter chip rail doubling as preset filters, inline quick-add row, bulk bar). Global keybinds are **`N` and `Esc` only**; `Enter` is bound per-input on the two quick-add fields (NOT global). **(3) Profile v2 + sidebar avatar live updates + Groups smart-picker** — `templates/pages/profile.html` is now 444 LOC with the `.pf2-*` namespace (hero banner, abs-positioned avatar, 4-variant role badge `--admin|manager|agent|viewer` where Team Lead/IT/HR all collapse into `--agent` color, click-anywhere inline-edit cards with `pf2-row--flash` save animation, 8s auto-redirect after ticket conversion). About **~478 LOC of new CSS** at `custom-v15.css:18723+`. Sidebar Emails entry uncommented; sidebar user avatar now image-aware via server-emitted `user.updated` LiveBus payload + `initSidebarUserLive` setting `style.backgroundImage`. Groups list smart member-picker enforces "one user per group per tenant" client-side AND in both `UserGroupSerializer.validate_member_ids` and `UserGroupViewSet.add_members` — both return a **single concatenated string in `detail`** (NOT the `{conflicts: [...]}` array some earlier notes claimed). The `docs/reference/*-surface.md` files were last regenerated 2026-05-22 against `ea87bb2` (commit state `241e407`) and **do not cover Inbox Hub, Profile v2, Reminders v2, Groups smart picker, sidebar avatar live updates, or any working-tree changes since that date**.
>
> **Working-tree facts vs HEAD**: `makemigrations --check` is **clean**. 8 migrations uncommitted: `accounts/0012`, `api_keys/0002`, `comments/0010`, `inbound_email/0009`, `inbox_hub/0001`, `notifications/0005`, `tenants/0009`, `tickets/0027`. Together they ship Phase-0+1A behind the `TenantSettings.inbox_hub_enabled` flag (default `False` → legacy auto-create-ticket flow runs unchanged). **Verified factual counts** (this refresh):
> - **91 Django model classes** across 21 apps with `models.py` (per-app: tickets 22, inbox_hub 8, accounts 8, knowledge 6, contacts 5, voip 5, analytics 4, billing 4, comments 4, kanban 3, inbound_email 3, messaging 3, newsfeed 3, agents 2, crm 2, custom_fields 2, notifications 2, tenants 2, api_keys 1, attachments 1, notes 1). The earlier confusion (agents=4, newsfeed=5, crm=4, custom_fields=4, notifications=3 from raw grep) was nested `TextChoices`/`Manager` subclasses — not new Django models.
> - **113 migrations total** across 21 apps; 8 uncommitted.
> - **23 `/api/v1/*/` `path(...)` includes** in `main/urls.py` (22 unique URLConfs — `inbound-email/` dual-mounts as `emails/`). +1 vs prior doc.
> - **35 frontend URL paths** in `apps/tenants/frontend_urls.py` (+1 — `inbox-hub/`).
> - **6 WebSocket consumers** at `ws/messaging/<id>/`, `ws/notifications/`, `ws/tickets/<id>/presence/`, `ws/tickets/feed/`, `ws/voip/events/`, `ws/live/`.
> - **24 Celery `@shared_task` functions** across 8 task modules; **9 in Beat schedule**.
> - **62 test modules total** (55 root + 7 app-level, incl. `tests/test_inbox_hub.py` with 13 passing tests).
> - **`static/css/custom-v15.css` is now 24,622 LOC** (HEAD 23,623; +999 net working-tree). The +999 = sidebar-avatar tweaks (~5 LOC), dashboard `.activity-list` flex-fill fix (~14 LOC), Profile v2 block (~478 LOC), audit-log redesign refinements (~1,268 LOC, much expanded since 241e407), Inbox Hub Phase 1 MVP block (~535 LOC at L24088-end), minus removed legacy `.pf-*` rules and v14 dead trees.
> - **48 `.html` templates** (+1: `pages/inbox_hub/list.html`); **18 `templates/pages/` subfolders** (+1).
> - **14 JS files / 4,734 LOC** in `static/js/` (+1: `inbox-hub.js` 666 LOC; `app.js` 843→880 +37 net for hub_email_* notif config, avatar handling, badge wiring).
> - **42 signal receivers** wired across 10 apps with `signals.py` (+ `notifications/signal_handlers.py`). `apps/tickets/signals.py` has 11; `apps/accounts/signals.py` has 5 (working-tree change adds 0 new receivers — only `_serialise_user` gained the `avatar` URL field).
> - **`logs/` is now ~54MB** (up from 41MB) — `celery-worker-error.log` at 29MB AND `celery-beat-error.log` newly above 10MB at 11MB. Still no rotation.

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
│   ├── agents/                    # AgentAvailability + CustomAgentStatus + load-fairness email agent picker
│   ├── analytics/                 # Reports, dashboard widgets, exports, calendar events
│   ├── api_keys/                  # APIKey model + auth class + viewset + per-key throttle + rate-limit-headers middleware + drf-spectacular extension
│   ├── attachments/               # File uploads (polymorphic GenericFK)
│   ├── billing/                   # Stripe billing, plans, subscriptions, webhooks, decorators
│   ├── comments/                  # Comments + Mention + CommentRead + ActivityLog (audit, 34 actions incl. 8 EMAIL_* for Inbox Hub) + LIVE signals
│   ├── contacts/                  # Contacts, Companies, Accounts, Groups, ContactEvent (360°) + LIVE signals
│   ├── crm/                       # Activity + Reminder (M2M contacts/tickets), lead/account scoring + LIVE signals
│   ├── custom_fields/             # EAV custom fields per tenant + sync signals
│   ├── inbound_email/             # SMTP+IMAP ingestion; forks at services.py:354-365 on TenantSettings.inbox_hub_enabled → legacy ticket-create OR park in Inbox Hub
│   ├── inbox_hub/                 # NEW: 8 models + services (park/convert/dismiss) + IsHubEmailAccessible row-scoping + HubEmailViewSet (list/retrieve/convert-to-ticket/dismiss)
│   ├── kanban/                    # Visual boards, columns, polymorphic CardPosition; drags route through tickets service (full audit/feed/SLA)
│   ├── knowledge/                 # KB articles, categories, search, stale alerts, gap digest, allowed_groups M2M
│   ├── messaging/                 # Real-time conversations (WS); Conversation.source_group; attachments on messages (POST broadcast action)
│   ├── nav/                       # URL-only helper (BadgeCountView — now 7 categories incl. inbox_hub)
│   ├── newsfeed/                  # Internal announcements, reactions, read receipts + LIVE signals
│   ├── notes/                     # Personal sticky notes (6 colors, pinning)
│   ├── notifications/             # In-app + email notifications + WebSocket (20 NotificationType values incl. 5 HUB_EMAIL_*)
│   ├── tenants/                   # Tenant model, middleware, frontend views, frontend_urls (35 paths), live broadcast layer, palette; TenantSettings.inbox_hub_enabled flag
│   ├── tickets/                   # Core ticketing; Queue gains optional department FK (Inbox Hub); SLA + business hours, CSAT, pipelines, macros, webhooks, deals
│   └── voip/                      # Asterisk ARI integration, SIP softphone, call logs, recordings, queues
├── main/                          # Django project root
│   ├── settings/{__init__,base,dev,prod}.py  # __init__ branches on DJANGO_DEBUG
│   ├── admin.py                   # SuperuserOnlyAdminSite + TenantFilteredAdmin mixin (full add/change/save with tenant picker)
│   ├── celery.py                  # Celery app + queue routing (6 globs + default), autodiscover_tasks()
│   ├── asgi.py                    # ProtocolTypeRouter: HTTP + WebSocket (6 consumer endpoints)
│   ├── context.py                 # contextvars-based tenant context (async-safe)
│   ├── models.py                  # TimestampedModel, TenantScopedModel
│   ├── managers.py                # TenantQuerySet, TenantAwareManager, SoftDeleteTenantManager
│   └── urls.py                    # 23 /api/v1/ includes (22 unique URLConfs; inbound-email dual-mounted at emails/) + /api/docs/
├── templates/                     # 48 .html files (18 subfolders under pages/)
│   ├── base.html                  # 265 lines — palette <style>, toast container, live-bus + live-connection JS, Flatpickr 3-CDN loader, sidebar-collapse FOUC fix
│   ├── includes/                  # 6 files — navbar, sidebar (+15 lines: Inbox Hub entry + avatar bg-image), softphone, messages, kb_sidebar_widget (orphan), page_back_button (now 11 sidebar paths incl. /inbox-hub/)
│   ├── pages/                     # 18 subfolders + 8 root html files (api_quickstart, calendar, dashboard, landing, login, profile, register, 403)
│   ├── landing/landing_crm.html   # Standalone marketing page (1,393 LOC; doesn't extend base.html)
│   └── {auth,knowledge,notifications,tickets}/email/  # 6 transactional email pairs
├── static/
│   ├── css/custom-v15.css         # 24,622 LOC (live file referenced by base.html — Crimson Black v9)
│   ├── css/custom.css             # 20,431 LOC (committed snapshot — NOT loaded; allowlisted in theme check)
│   ├── images/                    # Logo, favicon, hero artwork
│   └── js/                        # 14 vanilla-JS modules (~4,734 LOC, incl. live-bus.js + live-connection.js + inbox-hub.js)
├── tests/                         # 55 root pytest modules + 7 app-level (62 total) + tests/base.py legacy scaffold + test_inbox_hub.py (13 passing)
├── conftest.py                    # 16 factories + 20 fixtures (3 autouse: celery_eager, free_plan, clear_tenant_context)
├── pytest.ini                     # DJANGO_SETTINGS_MODULE=main.settings; pythonpath=. (3 lines, no asyncio_mode set)
├── requirements/{base,dev,prod}.txt   # prod.txt is literally `-r base.txt` — no extras
├── requirements.txt               # ROOT — byte-identical duplicate of requirements/base.txt
├── ecosystem.config.js            # PM2 prod: 5 processes
├── ecosystem.dev.config.js        # PM2 dev: 4 processes (no SMTP, watch-mode reloads)
├── Makefile                       # ~28 documented targets (logs-django declared but no rule body — calling it errors)
├── docs/                          # README + architecture.md (stale 2026-02-06) + reference/{4 docs} (regen 2026-05-22 @ ea87bb2 — all stale vs Inbox Hub) + ui-consistency-audit.md
├── tmp/emails/                    # Dev email capture (filebased EmailBackend)
├── logs/                          # PM2 log files — ~54MB and growing (celery-worker-error 29M, celery-beat-error 11M, django 9.2M, no rotation)
├── media/                         # User-uploaded: tenants/{id}/… and inbound_emails/{id}/…
├── scripts/                       # check_theme.py + .theme_baseline.json (147 hex literals across 11 files)
├── db.sqlite3                     # Dev database (~12MB)
├── celerybeat-schedule            # Celery Beat shelve file (built-in scheduler — django-celery-beat removed for Django 6 compat)
└── .env                           # 26 keys (.env.example covers only 16; 20 read-but-undocumented vars)
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
- **`apps/tenants/consumers.py::LiveEventConsumer`** (`GROUP_PREFIX="live_tenant"`). Anonymous → close 4001. No tenant → close 4001. Non-member → close 4003 (membership verified even for valid JWTs). Inbound messages silently ignored (read-only).
- **`apps/tenants/routing.py`** → `re_path(r"ws/live/$", LiveEventConsumer.as_asgi())`.

### Signal Emitters

| App | Receivers | Verbs |
|-----|-----------|-------|
| `accounts` | `TenantMembership.post_save/delete`, `Profile.post_save`, `User.post_save` (fans across every active membership; emits `avatar` URL in payload — working tree) | `membership.created/updated/deleted`, `profile.created/updated`, `user.updated` |
| `comments` | `Comment.post_save/delete` | `comment.created/updated/deleted` (payload `content_type="app_label.model"`, `object_id`) |
| `contacts` | `Contact/Company/Account/ContactGroup × post_save/delete` (ContactEvent intentionally skipped) | `contact.*`, `company.*`, `account.*`, `contact_group.*` |
| `crm` | `Activity/Reminder × post_save/delete`. Reminder verb resolved by state | `activity.*`, `reminder.created/updated/completed/cancelled/deleted` |
| `newsfeed` | `NewsPost.post_save/delete`, `NewsPostReaction.post_save/delete` | `newsfeed.created/updated/deleted`, `newsfeed.reacted` |
| `inbox_hub` (services-emitted, not signal-emitted) | `park_email_in_hub`, `convert_to_ticket`, `dismiss_hub_email` | `hub_email.created`, `hub_email.converted_to_ticket`, `hub_email.dismissed` |

**Tickets do NOT server-side broadcast to `live_tenant_*`** — `apps/tickets/services.py::broadcast_ticket_event` publishes only to `ticket_feed_{tenant_id}`. The bridge to LiveBus is **client-side** in `static/js/ticket-feed.js`.

App configs (`apps/{accounts,comments,contacts,crm,newsfeed}/apps.py`) import their `signals` module in `ready()`. **`apps/inbox_hub/apps.py` has NO `ready()`** — no signals.py exists yet.

### Frontend

- **`static/js/live-bus.js`** (175 LOC) — global `window.LiveBus`. API: `on/onMany/publish/debounce/rafBatch/isConnected/setChannelState`. Wildcard `"*"` subscriber gets all events. Cross-tab fan-out via optional `BroadcastChannel('kanzan-live')`. Handler errors caught + logged.
- **`static/js/live-connection.js`** (206 LOC) — global `window.LiveConnection`. Single shared `wss?://host/ws/live/`. Skips pre-auth pages and pages without a Django `sessionid` cookie. Exponential backoff 1s→30s with ±20% jitter, infinite retries. 25s heartbeat ping with 8s pong timeout. Reconnect → publishes `live.reconnected`. Visibility-hook: regaining focus while closed forces immediate reconnect.
- **Wiring in `templates/base.html`** — load order: Bootstrap → DOMPurify → live-bus.js (always) → api.js → app.js → command-palette.js → custom-select.js → conditional on `tenant and user.is_authenticated`: live-connection.js → agent-availability.js → notes-panel.js → keyboard-shortcuts.js → ticket-feed.js → (if `voip_enabled`) SIP.js CDN + voip-softphone.js. **`inbox-hub.js` is page-specific** — loaded only by `templates/pages/inbox_hub/list.html` via `{% block extra_js %}`.
- **Live-status pill in navbar** — `#liveStatusPill` surfaced by `app.js::initLiveStatusPill()` when any tracked channel (`live`, `notifications`, `ticket_feed`) was previously open and is now reconnecting/closed.
- **`app.js::initSidebarUserLive`** (working tree, +12 LOC): subscribes to `user.updated`, filters by `data-current-user-id` on `.sidebar-user`. Now sets `style.backgroundImage = 'url("…")'` + `.has-image` class when `payload.avatar` is present (with embedded-quote escaping). Falls back to text initial otherwise.
- **`ticket-feed.js`** continues to own `ws/tickets/feed/` and `LiveBus.publish('ticket.<verb>', …)` for sidebar/dashboard subscribers. Banner click → `ticket.show_pending {count}`. **Tenant-wide `ticket_assigned` Toast was removed**.

### Event Naming

`<domain>.<verb>` — domains: `user`, `membership`, `profile`, `comment`, `contact`, `company`, `account`, `contact_group`, `activity`, `reminder`, `newsfeed`, `ticket` (client-side normalisation), `notification`, `live`, `livebus`, **`hub_email`** (created/assigned/reassigned/transitioned/escalated/converted_to_ticket/dismissed — only 3 currently emitted server-side: `created`/`converted_to_ticket`/`dismissed`).

### Frontend subscribers (where each event drives UI)

| Page | Events | Handler |
|------|--------|---------|
| `dashboard.html` | `ticket.event`, `notification.received`, `newsfeed.*`, `live.reconnected` | Debounced 600ms refresh of stats + recent activity |
| `tickets/list.html` | `ticket.created`, `ticket.updated/.assigned/.closed/.deleted`, `ticket.show_pending` | Debounced reload (page 1 only) |
| `tickets/detail.html` | `comment.*` (filtered), `ticket.updated/.assigned/.closed`, `ticket.deleted`, `live.reconnected` | Refetch ticket/comments/activity |
| `contacts/list.html` | `contact.*`, `company.*`, `account.*`, `contact_group.*`, `live.reconnected` | Debounced 500ms list reload |
| `reminders/list.html` | `reminder.created/updated/completed/cancelled/deleted`, `live.reconnected` | Debounced 500ms refetch |
| `inbox_hub/list.html` | 7 `hub_email.*` events, `live.reconnected` | Debounced 400ms list+counts+detail refresh |
| `app.js` (global) | `user.updated`, `livebus.channel_state`, 7 `hub_email.*` events | Sidebar live updates; live-status pill; **inbox_hub badge refetch (debounced 500ms)** |

All page subscribers use a `document.visibilityState !== "hidden"` guard + `visibilitychange` listener.

### Channel-Layer Groups

- `live_tenant_{tenant_id}` — primary live events (newsfeed, CRM, memberships, contacts, comments, **hub_email**)
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

## Inbox Hub (Phase 0 + 1A backend + Phase 1 MVP frontend — uncommitted)

Email-to-Queue triage workspace. Reshapes inbound-email flow so messages land in a centralised Hub for agent triage instead of auto-creating tickets. **Default: OFF** — the seam at [apps/inbound_email/services.py:354-365](apps/inbound_email/services.py#L354) forks on `TenantSettings.inbox_hub_enabled` (BoolField, default `False`).

### Models (8 — `apps/inbox_hub/models.py`, 431 LOC)

- **`Department(TenantScopedModel)`** — `name`, `slug` (UniqueConstraint per tenant `ih_department_tenant_slug_uniq`), `description`, `lead` (FK User PROTECT), `members` (M2M via `DepartmentMembership`), `default_queue` (FK `tickets.Queue` SET_NULL), `business_hours` (FK `tickets.BusinessHours` SET_NULL), `is_active`. Index `(tenant, is_active)`.
- **`DepartmentMembership(TenantScopedModel)`** — through-model. `department`, `user`, `skills` (JSON list — empty until Phase 3 `SkillBasedStrategy`). UniqueConstraint `(department, user)`.
- **`HubEmail(TenantScopedModel)`** — the workspace entity. `inbound` 1:1 to `InboundEmail` CASCADE (`related_name="hub_email"`), `contact`, `department`, `queue`, `assignee` (all db_indexed). **9-state enum** (`NEW → ASSIGNED → IN_PROGRESS → PENDING_AGENT ⇄ AWAITING_CUSTOMER → ESCALATED → RESOLVED → CONVERTED_TO_TICKET | DISMISSED`) + **4-priority enum** (`low/normal/high/urgent`). SLA fields (`sla_response_due_at` db_indexed, `sla_resolution_due_at`, `response_breached`, `resolution_breached`, `first_responded_at`, `first_assigned_at`, `pause_started_at`, `total_pause_seconds`). Escalation: `escalation_count`, `escalated_to`. Terminal paths: `converted_ticket` (OneToOne to Ticket SET_NULL, `related_name="origin_hub_email"`), `dismissed_at`/`by`/`reason`. `auto_classification_data` JSONField (AI drop-zone). **5 indexes**: 4 composite on `(tenant, …, state)` + one partial `(tenant, sla_response_due_at)` filtered to active states.
- **`HubEmailAssignment(TenantScopedModel)`** — immutable audit row. `Reason` enum (`AUTO/MANUAL/ESCALATION/REASSIGNMENT`). `assigned_to` PROTECT, `assigned_by` null=system.
- **`HubEmailNote(TenantScopedModel)`** — internal agent note (`ordering=["created_at"]` ASC — old notes first; intentionally NOT carried over on conversion). `author` PROTECT.
- **`HubEmailSLA(TenantScopedModel)`** — per-(queue, priority) or per-(department, priority) policy. Both FKs nullable; **conditional UniqueConstraints** scope uniqueness to non-null queue/department (Postgres native; SQLite ≥3.30).
- **`RoutingRule(TenantScopedModel)`** — ordered IF/THEN rule consulted by RoutingEngine (Phase 1B+). `match` JSON: `{sender_domain[], subject_regex, recipient_local[], keyword[]}` (keys AND, values OR). Outputs: `department`, `queue`, `category`, `priority`, `stop_on_match`.
- **`QueueRouting(TenantScopedModel)`** — 1:1 supplement to `tickets.Queue`. `strategy_code` (pipe-delimited fallback chain, default `"availability_aware|least_loaded|round_robin"`) + `leave_unassigned_when_no_match`.

### The seam — [apps/inbound_email/services.py:354-365](apps/inbound_email/services.py#L354)

```python
if existing_ticket:
    _add_reply_to_ticket(inbound, existing_ticket, contact, system_user)
else:
    # SEAM: Inbox Hub fork. When the tenant has flipped
    # TenantSettings.inbox_hub_enabled, park the email for
    # agent triage instead of auto-creating a ticket.
    settings = getattr(tenant, "settings", None)
    if settings is not None and settings.inbox_hub_enabled:
        from apps.inbox_hub.services import park_email_in_hub
        park_email_in_hub(inbound, tenant, contact, system_user)
    else:
        _create_ticket_from_email(inbound, tenant, contact, system_user)
```

> **Variable-shadowing footgun**: the local `settings` rebinds the module-level `from django.conf import settings`. Sibling function `resolve_tenant_from_address` (which uses `django.conf.settings.IMAP_DEFAULT_TENANT_SLUG`) is unaffected because it's a separate function, but future code in `process_inbound_email` after the seam would silently get `TenantSettings | None`.

**Existing-thread reply** path always goes straight to the matching ticket regardless of the flag. The Hub triages NEW conversations only.

### Strategy 4 in `resolve_tenant_from_address` (NEW — same diff as seam)

[apps/inbound_email/services.py:169-174](apps/inbound_email/services.py#L169) — `settings.IMAP_DEFAULT_TENANT_SLUG` last-resort fallback so shared mailboxes (single Gmail polled for many tenants in dev, or a single default tenant in single-tenant prod) route somewhere instead of returning `None`. Independent of Inbox Hub but ships in the same diff.

### Phase 1A backend surface

- **`apps/inbox_hub/services.py`** (270 LOC): `park_email_in_hub(inbound, tenant, contact, system_user)` — idempotent via `get_or_create(inbound=…)`; sets `inbound.status = PARKED_IN_HUB`; writes `EMAIL_RECEIVED` ActivityLog; broadcasts `hub_email.created`; schedules `_post_park_hooks` via `transaction.on_commit`. **`_post_park_hooks` body is still `return`** — placeholder for Phase 1B routing+assignment+SLA init. `convert_to_ticket(hub_email, actor, *, queue, status, assignee, priority)` — idempotent; reuses `_create_ticket_from_email` (which now returns the ticket); applies overrides via single `save(update_fields=…)`; transitions to `CONVERTED_TO_TICKET`; writes `EMAIL_CONVERTED_TO_TICKET` log; broadcasts. `dismiss_hub_email(hub_email, actor, reason)` — idempotent; writes `EMAIL_DISMISSED` log; broadcasts `hub_email.dismissed`.
- **`apps/inbox_hub/permissions.py`** (78 LOC): `IsHubEmailAccessible` row-scoping — Manager+ unrestricted within tenant; Team Lead full visibility within own departments (`user.department_memberships ∪ user.led_departments`); Agent/IT/HR own-department + (`assignee=me OR state=NEW`). **Safety valve**: HubEmails with `department_id IS NULL` are visible to any user with at least one department membership (Phase 1A bridge until RoutingEngine lands).
- **`apps/inbox_hub/serializers.py`** (136 LOC): `HubEmailListSerializer` (compact, 20 fields), `HubEmailDetailSerializer` (+ nested `inbound` body), `ConvertToTicketSerializer` (all overrides optional, PKs validated via TenantAwareManager), `DismissSerializer` (reason only, default `""`, max 255 chars).
- **`apps/inbox_hub/views.py`** (156 LOC): `HubEmailViewSet(ListModelMixin, RetrieveModelMixin, GenericViewSet)`. `permission_resource = "hub_email"`. Permission stack `[IsAuthenticated, IsTenantMember, HasTenantPermission, IsHubEmailAccessible]`. Query-param chips: `state`, `priority`, `assignee=me|<uuid>`, `queue`, `department`. **`get_queryset` builds fresh per call** (NOT class-level `queryset=HubEmail.objects.all()` — see pitfall #70).
- **`apps/inbox_hub/urls.py`** + [main/urls.py:33](main/urls.py#L33): router-registered at `/api/v1/inbox-hub/hub-emails/`. Routes: `GET /` (list, paginated 50/page, search on subject/sender, ordering on created_at/priority/state), `GET /{id}/` (retrieve with nested inbound), `POST /{id}/convert-to-ticket/` (returns `{ticket, hub_email}` HTTP 201), `POST /{id}/dismiss/` (HTTP 200).
- **`apps/accounts/permissions.py::ACTION_MAP`** (+3 lines): `"convert_to_ticket": "convert"` and `"dismiss": "dismiss"`. Without these, `HasTenantPermission` would 403 these action methods.

### Phase 1 MVP frontend (NEW — uncommitted)

- **`templates/pages/inbox_hub/list.html`** (281 LOC) — extends `base.html`, includes `page_back_button.html`. Root `<div class="ih-shell" id="inboxHubShell" data-current-user-id="{{ user.id }}">`. 3-column grid: LEFT `.ih-nav` (3 sections: Workload `data-filter="all|mine|triage"` chips + State `data-state="new|assigned|in_progress|resolved"` + Terminal `data-state="converted_to_ticket|dismissed"`); MIDDLE `.ih-list` (header + search + skeleton-row loader + pagination); RIGHT `.ih-detail` (empty state with J/K/C/X kbd hints + loaded view with state/priority pills + sender meta + action bar + body div). Two modals: `#ihConvertModal` (Queue/Priority/Status/Assignee selects — **stale `<option value="medium">` at L213** that doesn't match `Ticket.Priority` choices `low/normal/high/urgent`) and `#ihDismissModal` (255-char reason textarea).
- **`static/js/inbox-hub.js`** (666 LOC vanilla IIFE) — boots only when `#inboxHubShell` present. State: `{items, counts{all,mine,triage}, page, next, prev, activeFilter, activeState, search, selectedId, selectedDetail, queues, statuses, currentUserId}`. **API calls**: list, count fetches (3 parallel: all/mine/triage), detail, queue/status choices, `POST convert-to-ticket/` (body `{queue_id?, status_id?, priority?}` — note: `assignee_id` is NOT sent even though modal has the field — **stale Phase 1 stub**), `POST dismiss/`. **LiveBus subscriptions**: 7 events (`hub_email.created/assigned/reassigned/transitioned/escalated/converted_to_ticket/dismissed`) all debounced 400ms — only 3 emitted today (forward-compat). Keybinds: J/K row nav, C convert, X dismiss, Esc deselect (disabled inside inputs/contentEditable/modal-open). **8-second auto-redirect** after convert success ([inbox-hub.js:464-472](static/js/inbox-hub.js#L464)) — UX trap to know about; Phase 2 will replace with Undo button. Defense-in-depth: `safeAssign()` runs DOMPurify on every innerHTML assignment; email body rendered via strict `BODY_SANITIZE_CONFIG` allowlist (allows `p/br/b/i/em/strong/u/a/ul/ol/li/blockquote/pre/code/div/span/hr/h1-h6/table/thead/tbody/tr/th/td/img` + `[href, src, alt, title, class]` only; no `<script>`/`<iframe>`/`on*`).
- **CSS section** `custom-v15.css:24088-24622` (~535 LOC, ends file). Header: `INBOX HUB (Phase 1 MVP) / 3-column command surface: LEFT nav · MIDDLE list · RIGHT detail.` Selectors: `.ih-shell` (3-col grid `220px minmax(0,1fr) minmax(0,1.5fr)` viewport-minus-topbar), `.ih-nav-*`, `.ih-list-*`, `.ih-row + .ih-state-dot--{new|assigned|in_progress|resolved}`, row chips (`.ih-row-priority--{high|urgent|low}`, `.ih-row-state--{new|assigned|in_progress|escalated|resolved|converted_to_ticket|dismissed}`), `.ih-detail-*`, `.ih-state-pill` + 7 state variants, `.ih-priority-pill` + 3, `.ih-body{-content,-plain,-empty}`, `.ih-convert-hint`, `.ih-dismiss-blurb`. Responsive at 1199px narrows nav rail; at 991px hides nav rail entirely. **Zero new hex literals** — entirely tokenized.
- **Frontend URL** [apps/tenants/frontend_urls.py:46](apps/tenants/frontend_urls.py#L46): `path("inbox-hub/", views.inbox_hub_page, name="inbox-hub")`. **View** [apps/tenants/frontend_views.py:795-797](apps/tenants/frontend_views.py#L795): `@_membership_required` (any member can load; no role gate — API enforces RBAC). **Sidebar** entry at [templates/includes/sidebar.html:32-36](templates/includes/sidebar.html#L32) as FIRST link inside the "Inbox" section (above Tickets/Emails/Messages); badge id `#sidebarBadgeInboxHub`. **page_back_button** [templates/includes/page_back_button.html:15](templates/includes/page_back_button.html#L15) added `'/inbox-hub/'` as the 11th sidebar path so Back button suppressed.
- **`apps/nav/views.py::BadgeCountView._inbox_hub_count`** ([L198-227](apps/nav/views.py#L198)) — counts rows in 5 active states (`NEW, ASSIGNED, IN_PROGRESS, PENDING_AGENT, ESCALATED`) tenant-wide for Manager+, narrowed to `Q(assignee=user) | Q(state=NEW)` for agents (mirrors `IsHubEmailAccessible` triage rule). Defensive: try-imports `HubEmail` → `ImportError` returns 0. **Bug**: uses `membership.role` not `membership.effective_role` ([nav/views.py:59](apps/nav/views.py#L59)) — temporary role grants don't influence badge agent-scoping (latent inconsistency with the project's "always use effective_role" mantra).
- **`static/js/app.js`** updates: `NOTIF_TYPE_CONFIG` ([L215-230](static/js/app.js#L215)) adds 5 `hub_email_*` entries (icons + tones). `badgeMap` ([L723](static/js/app.js#L723)) adds `inbox_hub: 'sidebarBadgeInboxHub'`. `notifTypeToBadge` map routes all 5 `hub_email_*` Notification types to the inbox_hub badge. **NEW LiveBus subscription block** ([L771-781](static/js/app.js#L771)) — `onMany` of the 7 hub events triggers a debounced (500ms) refetch of `/api/v1/nav/badge-counts/`. The comment: "hub_email.created fans out to every connected agent — bump the badge for any tenant member, not just the Notification recipient (those land on assign/escalate, not on park)."

### RBAC (12 new codenames — `apps/accounts/defaults.py` + migration `accounts/0012`)

12 codenames added at [defaults.py:91-103](apps/accounts/defaults.py#L91):
- `hub_email.{view, assign, reassign, convert, dismiss, reply, escalate, note}` (8)
- `department.{view, manage}` (2)
- `routing_rule.manage` (1)
- `hub_sla.manage` (1)

Per-role grants:

| Codename | Admin (via ALL_CODENAMES) | Manager | Team Lead | Agent | IT/HR | Viewer |
|---|---|---|---|---|---|---|
| All 12 | ✓ | ✓ | – | – | – | – |
| `hub_email.{view,convert,reply,escalate,note}` + `department.view` (6 agent-tier) | ✓ | ✓ | ✓ | ✓ | ✓ | – (`view` via ≤40 fallback) |
| `hub_email.{assign,reassign,dismiss}` (3 triage extras) | ✓ | ✓ | ✓ | – | – | – |

`provision_default_roles` only assigns perms on first-creation (`if created:`) — adding codenames to `*_CODENAMES` lists requires a data migration (which `accounts/0012` provides for existing tenants) or `permissions.set()`. **`apps/accounts/migrations/0012_seed_inbox_hub_permissions`** uses `role.permissions.add(*perms)` not `.set()` so operator customisations are preserved. Reversible via `backwards()`.

### Tests — [tests/test_inbox_hub.py](tests/test_inbox_hub.py) (365 LOC, **13 tests, all passing**)

3 service-layer test classes + 1 HTTP/RBAC test class + 1 fixture (`parked_hub_email`):
- `TestParkEmailInHub`: park creates HubEmail + marks PARKED_IN_HUB; park writes EMAIL_RECEIVED ActivityLog
- `TestConvertToTicketParity`: convert produces field-identical ticket vs legacy + `hub_ticket.created_by == hub_user` (vs `system_user` in legacy — intentional audit divergence); convert is idempotent; priority override applies
- `TestDismissHubEmail`: dismiss marks state + writes EMAIL_DISMISSED log with reason
- `TestHubEmailApiPermissions`: admin/manager can list/convert/dismiss; viewer denied convert (no `hub_email.convert` codename, ≤40 fallback only covers `view`); anon denied; cross-tenant isolation via `HubEmail.unscoped` row creation

### Out of scope (lands in Phase 1B+)

- `routing.py` (RoutingEngine), `assignment.py` (5 Strategy classes), `state_machine.py` (validate_transition + ALLOWED_TRANSITIONS), `signals.py` (the `_post_park_hooks` placeholder still empty), `tasks.py`, `admin.py`.
- assign / reassign / transition / escalate / reply / note API actions (codenames seeded but no viewset reads them).
- 5 `HUB_EMAIL_*` Notification creators — enum members exist + `app.js` maps them to badge/icons; **NO `Notification.objects.create(type=NotificationType.HUB_EMAIL_*)` anywhere in the codebase**.
- Department/RoutingRule/HubEmailSLA/QueueRouting viewsets.
- Default Department seeding on tenant creation (the safety valve in `IsHubEmailAccessible` bridges this until then).
- Backfill of historical `InboundEmail` rows when flag flips ON.

### Phase 1 MVP frontend bugs to know about

1. **8-second auto-redirect after convert** ([inbox-hub.js:464-472](static/js/inbox-hub.js#L464)) — Phase 2 will replace with Undo button. Could surprise users mid-edit.
2. **Convert modal `<option value="medium">`** at [list.html:213](templates/pages/inbox_hub/list.html#L213) doesn't match `Ticket.Priority` choices. Submitting "medium" would 400.
3. **Assignee dropdown never populated** — `loadConvertChoices` only fills queues+statuses. `submitConvert` payload doesn't send `assignee_id` even if it could. Defer to "Unassigned (or auto)" default.
4. **`BadgeCountView` uses `membership.role`**, not `effective_role` — temp role grants don't shift inbox_hub badge scoping.

## Models (91 model classes across 21 apps with models.py — `nav` is URL-only)

> Counted as `class X(<…>)` in `apps/*/models.py`, excluding `TextChoices/IntegerChoices`, `Manager`, `QuerySet`. Per-app totals at the top.

### Base Models (Abstract)
- **TimestampedModel**: UUID PK + `created_at` + `updated_at`; default ordering `["-created_at"]`.
- **TenantScopedModel**: TimestampedModel + `tenant` FK (CASCADE, editable=False, db_index=True) + auto-filtering.

### Tenants / Accounts

**tenants** (2): `Tenant` (name, slug unique, domain unique nullable, is_active, logo); `TenantSettings` (1:1; auth_method, SSO config, timezone, date_format, branding `primary_color`+`accent_color` with hex validators, `inbound_email_address`, business hours/days, `auto_close_days` (5), `csat_delay_minutes` (60), `auto_transition_on_assign`, `auto_send_ticket_created_email`, `auto_assign_inbound_email_tickets`, **`inbox_hub_enabled` (default `False`, migration 0009)**). Defaults: `primary_color="#6366F1"`, `accent_color="#F59E0B"`. Crimson Black `#C1121F`/`#E11D2D` is only the fallback in `apps/tenants/colors.py::derive_palette` when hex parsing fails.

**accounts** (8): `User(AbstractUser)` (email-based, UUID PK, `auth_version`, `avatar`, `phone`, `username=None`, `is_service_account` for hidden API-key users). `Permission` — **global** (not tenant-scoped); `Action` enum (7 members: view/create/update/delete/assign/export/manage). `Role(TenantScopedModel)` — M2M `permissions`, `hierarchy_level` default 100, `is_system`. `Profile(TenantScopedModel)` — UI/agent prefs. `TenantMembership` — NOT TenantScoped (joins user↔tenant); UUID PK; FKs `user`, `tenant`, `role` (PROTECT), `temporary_role`, `temporary_role_granted_by`, `invited_by`; fields `temporary_role_expires_at`, `temporary_role_granted_at`, `is_active`; **M2M `temporary_permissions` → Permission** (curated allow-list — empty = full temp role perms; non-empty = intersection of `temporary_role.permissions ∩ temporary_permissions`); methods `has_active_temporary_role`, `effective_role`, `get_effective_permissions_qs()`, `has_effective_permission(codename)`. `Invitation(TenantScopedModel)`. `UserGroup(TenantScopedModel)` (migration 0009; M2M `members`; **NOT in admin**; used by `Article.allowed_groups`; "one user per group" rule enforced in serializer + viewset). `EmailVerificationToken`.

### Tickets (22 model classes — heaviest app)

`Pipeline`, `PipelineStage`, `TicketStatus` (incl. `pauses_sla`, `is_closed`, `is_default`), `Queue` (`default_assignee`, `auto_assign`, **`department` FK to inbox_hub.Department SET_NULL** — migration 0027), `TicketCategory`, `TicketCounter` (NOT TenantScoped; OneToOne tenant; SELECT FOR UPDATE + F-expression), `Ticket` (~64 fields; soft delete; CSAT; deal fields; `merged_into`; `auto_close_task_id`; `pre_wait_status`; `tags`+`custom_data` JSON). **`Ticket.save()` auto-populates `company_id`** from linked Contact's company when not explicitly set ([tickets/models.py:601-612](apps/tickets/models.py#L601)). `TicketLink` (4 types + circular guard via BFS), `SLAPolicy`, `EscalationRule`, `BusinessHours` (IANA timezone + schedule JSON), `PublicHoliday`, `SLAPause`, `TicketActivity` (**27 event choices**), `CannedResponse`, `Macro`, `SavedView`, `TicketAssignment` (immutable audit), `TicketWatcher` (4 reasons, `is_muted`), `TimeEntry` (1–1440 mins), `TicketTemplate`, `Webhook` (HMAC SHA-256, 8 EventType members, auto-disable at 10 failures).

> Admin registers 17 of 22 — TicketLink, TicketCounter, Macro, TicketActivity are NOT in admin.

### Contacts (5)

`Account` (CRM account; `mrr`, `health_score` clamped 0–100), `Company` (name unique per tenant), `Contact` (email unique per tenant, `email_bouncing` indexed, `lead_score` 0–100, `last_activity_at` indexed, `source` 6-choice), `ContactGroup` (M2M contacts), `ContactEvent` (append-only 360° timeline; `source` 4-choice; intentionally NOT live-broadcast). **ContactEvent NOT in admin.**

### CRM (2)

`Activity` (call/email/meeting/task), `Reminder` (formerly `Recall`; **M2M `contacts`/`tickets`** since migration 0004; priority; `status` is **derived property** of completed_at/cancelled_at/scheduled_at; `unscoped` manager; `ReminderQuerySet.overdue()/pending()/for_user()`; methods `mark_completed/mark_cancelled/reschedule`).

### Inbound Email (3)

`InboundEmail` extends `TimestampedModel` (NOT TenantScopedModel — tenant nullable, resolved post-parse). `Status` (**9 members** incl. `PARKED_IN_HUB` from migration 0009). `Direction` unified inbound+outbound; `SenderType` (customer/system/agent); `InboxStatus` (4); `InboxAction` (3). Threading: `message_id` (indexed, stored without `<>`), `in_reply_to`, `references`. Idempotency keys: `"in:{tenant_id}:{message_id}"`. `is_read` indexed (migration 0007). `save()` enforces immutability of `linked_at/by` + `actioned_at/by` once set. `BounceLog` for hard bounces. `IMAPPollState` (`uid_validity`+`last_uid` watermark). **Only InboundEmail in admin** — BounceLog and IMAPPollState are not.

### Knowledge (6)

`Category`, `Article` (status: draft/pending_review/published/rejected/flagged; visibility: internal/public; review workflow + Postgres FTS via `SearchVectorField` + GinIndex; PDF/DOCX via mammoth + sanitisation; **`allowed_groups` M2M to UserGroup** — migration 0005; auto-slug with collision suffix via `Article.unscoped` scan). **`Article.save()` resolves tenant** from `main.context.get_current_tenant()` before slug scan, falls back to `"article"` when `slugify(title)` empty. `KBRevision`, `KBVote` (session_key-keyed), `KBSearchGap`, `KBTicketLink`. **Only Category + Article in admin.**

### Kanban (3)

`Board` (`resource_type` TICKET/DEAL, `is_default`, `is_personal` migration 0004 — private to creator), `Column` (board, order, optional status FK, wip_limit, color), `CardPosition` (polymorphic GenericFK; unique on column+content_type+object_id).

### Comments / Messaging / Newsfeed / Notifications / Inbox Hub

**comments** (4): `Comment` (polymorphic GenericFK, threaded, `is_internal`), `Mention`, `CommentRead`, `ActivityLog` (**34 action choices** after migration 0010 — adds 8 `EMAIL_*` for Inbox Hub).

**messaging** (3): `Conversation` (DIRECT/GROUP/TICKET; FK `source_group` to UserGroup migration 0002), `ConversationParticipant`, `Message` (threaded; mentions M2M; `is_edited`).

**newsfeed** (3): `NewsPost` (5 categories), `NewsPostReaction` (6 emoji), `NewsPostRead` (NOT tenant-scoped — row existence = read).

**notifications** (2): `Notification` (**20 NotificationType choices** after migration 0005 — adds 5 `HUB_EMAIL_*`); `NotificationPreference`. **`Notification` is NOT polymorphic** — only a `data` JSONField.

**inbox_hub** (8): see §Inbox Hub above.

### Agents / Custom Fields / Billing / Analytics / Attachments / Notes / API Keys / VoIP

**agents** (2): `AgentAvailability` (online/away/busy/offline + `custom_status` FK; load fields + working hours JSON + `auto_away_outside_hours`); `CustomAgentStatus` (migration 0006; tenant-scoped custom statuses; `StatusColor` 8-choice). `BUILTIN_STATUS_SLUGS` frozenset. **CustomAgentStatus NOT in admin.**

**custom_fields** (2): `CustomFieldDefinition` (8 field types × 3 modules; M2M `visible_to_roles`), `CustomFieldValue` (EAV; 4 typed value columns).

**billing** (4): `Plan` (tiered + `has_voip`, `has_call_recording`, `max_calls_per_month`, `audit_retention_days` from migration 0002), `Subscription` (1:1 Tenant; 6 status choices; 7-day `in_grace_period`), `Invoice`, `UsageTracker`.

**analytics** (4): `ReportDefinition`, `DashboardWidget`, `ExportJob` (CSV/XLSX/PDF), `CalendarEvent` (`color`/`end_date` migration 0003). **CalendarEvent NOT in admin.**

**attachments** (1): `Attachment` (polymorphic GenericFK; `tenants/{id}/attachments/YYYY/MM/{filename}`).

**notes** (1): `QuickNote` (6 colors; pinning; per-user).

**api_keys** (1): `APIKey(TenantScopedModel)` — `name`, `service_user` (1:1 hidden `User` with `is_service_account=True`), `role` (FK PROTECT — drives `HasTenantPermission`), `prefix` (indexed), `hashed_key` (SHA-512 hex; cleartext never persisted), `created_by` PROTECT, `is_active`, `expires_at`, `last_used_*`, `request_count`. Cleartext format: `kz_live_<slug6>_<token_urlsafe(32)>`. Sister files: `authentication.py` (DRF auth; cross-tenant guard; timing-safe `secrets.compare_digest`), `services.py` (mint/regenerate/revoke + ActivityLog + `on_commit` email task), `throttling.py` (`SimpleRateThrottle` subclass; per-`APIKey.pk` bucket; `1000/hour`), `middleware.py` (`RateLimitHeadersMiddleware` — emits `X-RateLimit-Limit/Remaining/Reset`), `extensions.py` (drf-spectacular `APIKeyAuthScheme`; registered via `apps.py::ready()`), `views.py`, `tasks.py`.

**voip** (5): `VoIPSettings` (singleton; encrypted ARI creds; STUN/TURN; `pjsip_context`), `Extension` (sip_username **globally unique**, encrypted password), `CallLog` (direction 3-choice, status 9-choice, indexed `asterisk_channel_id`), `CallRecording` (1:1 CallLog; `tenants/{id}/recordings/YYYY/MM/{uuid}.{ext}`), `CallQueue` (5 ACD strategies + M2M Extension members).

### Polymorphic (GenericFK) Models — 5 total

`Attachment`, `Comment`, `ActivityLog`, `CustomFieldValue`, `CardPosition`. **Not** Notification (data JSONField only).

## Role-Based Access Control

**Hierarchy:** Admin(10) → Manager(20) → **Team Lead(25)** → Agent(30) / **IT(30)** / **HR(30)** → Viewer(40).

**Default role seeding (`apps/tenants/signals.py::create_default_roles`)** runs on `Tenant.post_save (created=True)` and seeds **all seven** system roles inline. All `is_system=True`. Permission sets for the **six** perm-bearing roles (Viewer is intentionally permission-less, leans on ≤40 view fallback) come from `apps/accounts/defaults.py::ROLE_DEFINITIONS` (6 entries). Backfill for existing tenants via data migration `accounts/0011_seed_team_lead_it_hr_roles` (idempotent). The new 12 Inbox Hub permissions are backfilled via `accounts/0012_seed_inbox_hub_permissions` (uses `.add(*perms)` not `.set()`).

- `is_admin`: `hierarchy_level ≤ 10`; `is_admin_or_manager`: `≤ 20`; `is_agent_or_above`: `≤ 30`. **Team Lead (25)** satisfies `is_agent_or_above` but NOT `is_admin_or_manager`. **IT/HR (30)** satisfy `is_agent_or_above`. Viewer (40) satisfies none.
- Non-manager row-scoping (`level > 20`): the membership sees only own/assigned tickets, linked contacts, filtered kanban cards, own reminders/activities. Applies to Team Lead, Agent, IT, HR, Viewer.
- **Always use `TenantMembership.effective_role`** — temporary role wins until `temporary_role_expires_at`. Exception: `apps/nav/views.py::BadgeCountView` currently uses `membership.role` ([nav/views.py:59](apps/nav/views.py#L59)) — latent inconsistency.
- **`AgentAvailabilityViewSet.assignable_roles`** **excludes the `admin` slug** to prevent privilege escalation through the UI. Each returned role dict includes a `description` field.
- **Permission classes** (`apps/accounts/permissions.py`):
  - `HasTenantPermission` — codename-based; `ACTION_MAP` maps 70+ DRF action names to `{resource}.{action}`; includes `convert_to_ticket → convert` and `dismiss → dismiss` for Inbox Hub. Hierarchy fallback when membership has no permissions: `view → ≤40`, `create/update → ≤30`, **all others (incl. `convert`, `dismiss`) → ≤20**.
  - `IsTicketAccessible` — object-level row filtering for agents (≤20 bypass; otherwise `created_by_id == user.pk OR assignee_id == user.pk`).
  - `IsTenantMember`, `IsTenantAdmin`, `IsTenantAdminOrManager`.
  - Helper `_get_membership()` caches on `request._cached_tenant_membership`.
- `_role_required(20)` decorator gates admin/manager frontend pages. **Team Lead (25) does NOT pass `_role_required(20)`** by design. `_role_required(30)` gates Outbound Emails (admits Team Lead/Agent/IT/HR). **`/settings/`** is `@_membership_required + @ensure_csrf_cookie` — any member can load; API enforces admin-only writes (with per-field allowlist for Managers).

## Signals (10 apps with signals.py + notifications/signal_handlers.py)

**42 total receivers** wired up. Apps with `signals.py`: accounts, comments, contacts, crm, custom_fields, knowledge, newsfeed, tenants, tickets, voip. `notifications` uses `signal_handlers.py`. **`apps/inbox_hub/` has NO `signals.py` yet** — `apps.py` has no `ready()` method.

### Tenants — 2 receivers
- `Tenant.post_save (created=True)` → `create_tenant_settings` + `create_default_roles` (seeds 7 system roles inline).

### Accounts — 5 receivers (working-tree change adds 0 new receivers; only `_serialise_user` extended)
- `TenantMembership.post_save` → `create_profile_on_membership` + `broadcast_membership_save`
- `TenantMembership.post_delete` → `broadcast_membership_delete`
- `Profile.post_save` → `broadcast_profile_save`
- `User.post_save` → `broadcast_user_save` (skips creation; on update fans across every active membership's tenant group; **NOW emits `avatar` URL in payload** via `_serialise_user` working-tree change — try/except guards `ImageField.url`)

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
2. **ActivityLog** — polymorphic audit trail with diffs+IP, **34 actions** (after migration 0010 — 26 pre-Inbox-Hub + 8 `EMAIL_*` for Inbox Hub). Endpoint: `/api/v1/tickets/tickets/{id}/activity/`.

**Dedup pattern:** The signal `log_ticket_activity` checks `instance._skip_signal_logging`. **Service-layer functions** (`assign_ticket`, `change_ticket_status`, `escalate_ticket`, `change_ticket_priority`) set this flag before their `ticket.save(update_fields=…)`. ViewSets also set it; use `serializer.instance` in `perform_update` so the flag persists. 2-sec window in signal.

**Service layer** (`apps/tickets/services.py`) — every mutation writes to BOTH logs atomically and broadcasts WebSocket events via `transaction.on_commit()`. Public: `create_ticket_activity`, `assign_ticket`, `transition_ticket_status`, `change_ticket_status`, `change_ticket_priority`, `log_ticket_comment`, `close_ticket`, `escalate_ticket`, `merge_tickets`, `split_ticket`, `bulk_update_tickets`, `apply_macro/render_macro`, `record_first_response`, `transition_pipeline_stage`, `initialize_sla`, `log_sla_change`, `broadcast_ticket_event`, `validate_status_transition`, `resume_from_wait`. **`ALLOWED_TRANSITIONS["waiting"] = ["open","in-progress","resolved","closed"]`** — Waiting→Resolved/Closed legal.

**Kanban drags route through services.** `apps/kanban/services.py::move_card(card_position, target_column, position, *, actor=None, request=None)` — when dragged card is a `Ticket` AND target column has a different status, calls `apps.tickets.services.change_ticket_status(content_obj, target_column.status, actor, request=request)`. Full dual-write + ticket-feed broadcast + SLA pause handling.

**Kanban orphan-card cleanup** (`79eeb88`): `apps/tickets/signals.py:642-675` registers `post_delete` receiver that hard-deletes `CardPosition` rows via `CardPosition.unscoped`. **Companion**: `apps/kanban/serializers.py` skips cards whose resolved content object is `None`.

**Inbox Hub dual-write parity** (after Phase 1A): `park_email_in_hub`, `convert_to_ticket`, `dismiss_hub_email` each write an `ActivityLog` row (action `EMAIL_RECEIVED` / `EMAIL_CONVERTED_TO_TICKET` / `EMAIL_DISMISSED`) AND broadcast a `hub_email.*` LiveBus event on commit. No `TicketActivity` writes — HubEmail has its own log surface via `HubEmailAssignment` (immutable audit row, future use).

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

## Inbound / Outbound Email

### Inbound (`apps/inbound_email/`)
- **In-process SMTP server** via `aiosmtpd`, launched by `run_smtp_server` (PM2 process `kanzan-smtp`). Optional STARTTLS + LOGIN/PLAIN AUTH.
- **IMAP poller** — shared Gmail-style mailbox; UID > watermark (not UNSEEN). Driven by `fetch_inbound_emails_task` (Celery Beat 60s). Disabled when `IMAP_HOST` blank. **Safety: never backfills** — aborts on UIDVALIDITY/UIDNEXT parse failure.
- **Tenant resolution** — 4 strategies in `resolve_tenant_from_address`:
  1. Plus-addressing (`support+{slug}@…`)
  2. Subdomain routing
  3. Custom `TenantSettings.inbound_email_address`
  4. **NEW (working tree):** `settings.IMAP_DEFAULT_TENANT_SLUG` last-resort fallback — shared mailboxes route to a default tenant
- **Filters** run BEFORE tenant resolution: loop detection, noreply senders, RFC 3834 Auto-Submitted / Precedence: bulk/junk/list. `classify_email() → bounce/auto_reply/loop/legitimate`. Bounces write `BounceLog` and flip `Contact.email_bouncing=True`.
- **Threading** — `find_existing_ticket` 3-tier: In-Reply-To → References (reversed) → subject `[#N]` regex.
- **Processing pipeline** (`process_inbound_email_task`, max_retries=3, default_retry_delay=30s, acks_late): `select_for_update` → filter classifier → tenant resolution → idempotency claim → find/create contact → find existing ticket OR (per the seam) `park_email_in_hub` if `inbox_hub_enabled` else `_create_ticket_from_email` (with `_maybe_auto_assign`) → attach files → queue confirmation email via `transaction.on_commit()`.
- **Agent inbox workflow** (`inbox_services.py`): `link_email_to_ticket`, `action_email`, `ignore_email`.

### Outbound (`apps/tickets/email_service.py`)
- `send_ticket_email()` — single entry point. RFC-compliant Message-IDs.
- Persists an OUTBOUND `InboundEmail` record for threading.
- Dev: `filebased.EmailBackend` → `tmp/emails/`. Prod: SMTP.

## Auto-Assign (Inbound Email → Agent)

`apps/agents/services.py::pick_email_agent(tenant)`:
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
- **`DEFAULT_AUTHENTICATION_CLASSES` order ([base.py:212-216](main/settings/base.py#L212)):** `JWTAuthentication` → `APIKeyAuthentication` → `SessionAuthentication`. JWT tried FIRST.
- **Frontend:** Session auth (Redis cached_db, host-only cookie).
- **SSO:** django-allauth (Google, Microsoft, OIDC) — `ACCOUNT_LOGIN_METHODS = {"email"}` (a set). `django.contrib.sites` NOT in INSTALLED_APPS; allauth ≥65 runs without it.
- **Global logout:** `User.auth_version` bumped invalidates all prior sessions via `SessionVersionMiddleware`.

### `/api/v1/` Endpoint Map (23 router includes / 22 unique URLConfs — `inbound-email/` dual-mounts as `emails/`)

```
/tenants/            TenantViewSet (slug lookup), TenantSettingsViewSet (singleton; per-field Manager allowlist)
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
/inbound-email/      InboundEmailViewSet (read-only) + InboxViewSet (link/action/ignore)
/emails/             alias mount of inbound_email.api_urls (namespace="emails_api")
/crm/                ActivityViewSet (+my-tasks), ReminderViewSet (+overdue/stats/complete/cancel/reschedule/bulk-action), PipelineForecastView
/nav/                BadgeCountView (7 categories incl. inbox_hub; capped at 99 per category)
/newsfeed/           NewsPostViewSet (+react/mark-read/mark-all-read/unread-count)
/voip/               VoIPSettingsViewSet, ExtensionViewSet, CallLogViewSet (+active/stats), InitiateCallView, CallHoldView, CallTransferView, CallHangupView, SIPCredentialsView, CallRecordingDownloadView, CallQueueViewSet
/inbox-hub/          HubEmailViewSet (list/retrieve/convert-to-ticket/dismiss; NEW Phase 1A)
```

**Non-HTTP inbound channel:** `kanzan-smtp` PM2 process at `SMTP_SERVER_HOST:SMTP_SERVER_PORT` (default `0.0.0.0:2525`) feeds the same `InboundEmail` + Celery pipeline.

**Docs:** `/api/docs/` (Swagger UI — shows both `ApiKeyAuth` and JWT Bearer in Authorize dialog), `/api/schema/` (OpenAPI 3.0 JSON).

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
/inbox-hub/                   inbox_hub_page        @_membership_required      ← NEW (working tree)
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
6. **Live:** `ws/live/` → `LiveEventConsumer`. Group: `live_tenant_{tenant_id}`. Anon → close 4001; non-member → close 4003.

## Celery Tasks & Beat Schedule

### Queue Routing (`main/celery.py`)
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

### Beat Schedule (9 tasks)

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

Celery Beat uses the **built-in shelve scheduler** (`celerybeat-schedule`). `django-celery-beat` removed (Django 6 incompat).

`apps.crm.tasks.check_overdue_reminders` and `apps.tickets.tasks.check_sla_breach_warnings` exist but are **NOT in Beat**.

### Task Inventory (24 tasks across 8 modules)

- **notifications**: `send_notification_email` (retries=3, default_retry_delay=60s, acks_late, kanzan_email), `cleanup_old_notifications`
- **analytics**: `process_export_job` (retries=3; CSV/XLSX; openpyxl optional → CSV fallback)
- **inbound_email**: `fetch_inbound_emails_task`, `process_inbound_email_task` (retries=3, default_retry_delay=30s, acks_late)
- **tickets**: `check_sla_breaches`, `check_overdue_tickets`, `send_ticket_reply_email_task`, `send_ticket_created_email_task`, `send_ticket_email_task`, `auto_close_ticket` (two-guard idempotency), `send_csat_survey_email`, `deliver_webhook_task` (exp backoff), `check_sla_breach_warnings`, `propagate_sla_policy_change_task`
- **voip**: `process_call_recording`, `cleanup_stale_calls`, `sync_call_state` (queue `kanzan_voip`)
- **crm**: `check_overdue_reminders` (NOT in Beat), `calculate_lead_scores`, `calculate_account_health_scores`
- **knowledge**: `alert_stale_articles`, `send_gap_digest` (registered as `knowledge_base.*`)
- **api_keys**: `send_api_key_created_email_task` (bind, retries=3, default_retry_delay=60s, acks_late)

**`apps/inbox_hub/` has NO `tasks.py`** — Phase 1A is no-task. No SLA breach checks, no escalation timers, no post-park hooks fire today.

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

> **Prod worker `-Q` list is `kanzan_default,kanzan_email,kanzan_webhooks`.** The `kanzan_voip` queue is in `main/celery.py` routes but the default worker doesn't subscribe; add it or start a dedicated VoIP worker. `run_ari_listener` is **not in PM2** by default.
> **Makefile `stop`/`restart` omit `kanzan-smtp`** — manage independently with `pm2 stop kanzan-smtp`.

### `ecosystem.dev.config.js` (dev, venv at `env/` symlinked to `.venv/`) — 4 processes
- `kanzan-django` runs `manage.py runserver 0.0.0.0:8001` (auto-reload).
- `kanzan-celery-worker` `-c 2 --max-tasks-per-child=50`, **watch enabled** on `apps/*/tasks.py`, `apps/*/services.py`, `main/celery.py` with 2s delay, 1GB.
- `kanzan-celery-beat`, `kanzan-flower` (same as prod, lower memory).
- **No `kanzan-smtp` in dev.** Common: `max_restarts=50`, `min_uptime="3s"`.

## Frontend Architecture

### JavaScript (`static/js/`, 14 modules, ~4,734 LOC — vanilla, no React/Vue)

| Module | LOC | Role |
|--------|----:|------|
| `app.js` | 880 | Global init: alerts, sidebar collapse, density, notification WS, Toast, `Kanzan.formatDate/formatDateTime/timeAgo`, sidebar badge polling (7 categories incl. inbox_hub), `initLiveStatusPill()`, `initSidebarUserLive()` (avatar bg-image support), `initSidebarBadges()` (LiveBus subs for hub_email.* events with 500ms debounced refetch). Bell/flyout animations (3s auto-fade) |
| `voip-softphone.js` | 710 | SIP.js 0.21.2 + `CallEventConsumer`. Dial pad, DTMF, mute/hold/transfer/hangup, incoming-call modal |
| `inbox-hub.js` | **666** | NEW vanilla IIFE controller for `/inbox-hub/`. State machine, 7 LiveBus subs (debounced 400ms), keybinds J/K/C/X/Esc, DOMPurify-sanitized email body rendering, 8s auto-redirect after convert |
| `custom-select.js` | 371 | `KanzenSelect` global with portal rendering + searchable when >8 options |
| `command-palette.js` | 337 | Cmd+K modal |
| `keyboard-shortcuts.js` | 318 | Global hotkeys (j/k/Enter/Esc/a/s/x/c/?, g d/t/c/b go-to). Injects runtime `<style>` using `var(--crm-primary)` |
| `ticket-feed.js` | 248 | WebSocket `ws/tickets/feed/`. Auto-connects via `data-ticket-feed` or URL match. Banner + row pulse. Publishes into LiveBus |
| `notes-panel.js` | 238 | Quick notes CRUD (6 colors, pinning, localStorage) |
| `agent-availability.js` | 227 | Status toggle + persistence |
| `live-connection.js` | 206 | Single shared `wss?://host/ws/live/`, 25s heartbeat / 8s pong, infinite backoff |
| `rich-editor.js` | 191 | TipTap wrapper. Page-specific load. TipTap via importmap from `esm.sh` (inline in `tickets/detail.html` L987-1000) |
| `live-bus.js` | 175 | Global pub/sub `window.LiveBus` |
| `api.js` | 90 | Central API client (CSRF from cookie + meta fallback, session credentials, JSON + multipart) |
| `theme.js` | 77 | light/dark/system (default dark). Loaded SYNCHRONOUSLY in `<head>` (FOUC prevention) |

### CSS

- **`static/css/custom-v15.css`** — **24,622 LOC** (HEAD 23,623; +999 net working-tree). Crimson Black v9 design system. Latest sections:
  - **PROFILE v2** at L18727 (~478 LOC, `.pf2-*` namespace — hero banner, inline-edit cards, role badges, `pf2-row--flash` save animation; only 4 role variants — Team Lead/IT/HR collapse to `--agent`)
  - **AUDIT LOG REDESIGN** at ~L22820 (~1,268 LOC, Insights ribbon, live pulse, heat ribbon, top contributor, risk signal, color-coded action chips)
  - **Flatpickr Crimson theme** at ~L22518 (globally loaded — all datetime-local inputs render with palette)
  - **GLOBAL ANIMATION POLISH** at ~L22755 (`@keyframes kz-fade-up`/`kz-fade-in`)
  - **INBOX HUB Phase 1 MVP** at L24088-end (~535 LOC, 3-column command surface, full state/priority chip variants, responsive collapse)
  - Token scales (committed 3c99a85): `--crm-font-size-*`, `--crm-weight-{normal:400,medium:500,semibold:600,bold:700}`, `--crm-z-{base,sticky,dropdown,modal,popover,tooltip,flyout:1080,overlay:1085,toast:1090}`, `--crm-radius-{xs:4px,sm:8px,md:6px,lg:14px,xl:16px,pill:9999px}`
- **`static/css/custom.css`** — 20,431 LOC committed snapshot, NOT loaded, allowlisted in theme check.

### Theming Architecture

- **`apps/tenants/colors.py`** (159 LOC) derives **~21-key palette** from primary+accent hex (defaults `#C1121F`/`#E11D2D` Crimson Black if `TenantSettings` fields unset/malformed): 50–900 scale, hover/active/dark/light/subtle/ring/rgb variants, **`text_on_primary`/`text_on_accent`** picked by WCAG 2.x luminance (white vs `#0B0B0B`, whichever wins ≥ 4.5:1). `logger.warning` if final contrast < AA.
- **`base.html` palette block** (L30-86) emits ~35 CSS vars on `:root, [data-bs-theme="light"], [data-bs-theme="dark"]`.
- **No hex literals in rule bodies.** Token blocks are the only place hex is permitted. **`make theme-check`** enforces against `scripts/.theme_baseline.json`.
- **JS color dicts** use `'var(--crm-*)'` literals — browsers resolve var() at CSS-value time when assigned via inline style.
- **`withAlpha(color, percent)` helper**: when input is a `var()`, uses `color-mix(in srgb, <color> Y%, transparent)`; falls back to hex+suffix concat.
- **Dashboard chart color map**: `STATUS_COLORS` / `PRIORITY_COLORS` route through `--status-*-dot` tokens (NOT `--crm-primary/accent`).
- **Chart.js compatibility**: 2D canvas doesn't resolve var(); charts use `cssVar(name, fallback)` + `resolveColor()` reading via `getComputedStyle`.

**Theme baseline** (`scripts/.theme_baseline.json`): **147 hex literals across 11 files** (custom-v15.css 81 + landing/landing_crm.html 21 + dashboard.html 13 + landing.html 15 + contacts/list.html 5 + kanban/board.html 4 + auth/verify_email_sent.html 3 + tickets/detail.html 2 + login.html 1 + reminders/list.html 1 + tickets/list.html 1).

Allowlist files: `apps/tenants/colors.py`, `scripts/check_theme.py`, `templates/pages/settings/tenant.html`, `static/js/theme.js`, `static/css/custom.css`. Allowlist dirs: 4 email template dirs, `tests/`, `.venv/`, `env/`, `node_modules/`, `.git/`. Theme-check masks: CSS comments + `:root`/`[data-bs-theme]` blocks + HTML `{% %}` tags + `<input type="color">` + `data-*="hex"` attrs. **Inside `<script>` blocks JS comments masked but body code visible** (since `22f284a`). `.js` files: only JS comments masked — string-literal hex IS flagged.

### Templates (48 .html files total)

- `templates/base.html` (265 lines) — palette `<style>`, toast container, quick-notes panel, softphone (conditional), DOMPurify 3.2.4 CDN + SIP.js 0.21.2 CDN (conditional) + Flatpickr 3-CDN-fallback global loader + mobile detection IIFE + synchronous `kanzan_sidebar_collapsed` localStorage check pre-paint. Default theme: dark.
- `templates/includes/` (6 files):
  - `navbar.html` (189 lines) — `#liveStatusPill`, theme toggle, availability dropdown, quick-create, notes toggle, notification cluster (`.notif-bell-btn` + `.notif-flyout` 3s auto-fade)
  - `sidebar.html` (160 lines, **working-tree modified +15 lines**): **(a)** Inbox Hub link added at L32-36 as FIRST link in Inbox section with `#sidebarBadgeInboxHub` badge slot; **(b)** Emails link uncommented; **(c)** Avatar gets `.has-image` class + inline `style="background-image: url('{{ user.avatar.url }}')"` when user has avatar (text fallback only when no avatar)
  - `softphone.html` (169 lines) — floating widget, conditional
  - `messages.html` (21 lines) — Django messages iterator
  - `kb_sidebar_widget.html` (158 lines) — **ORPHAN** (no template includes it; safe-deletion candidate)
  - `page_back_button.html` (21 lines, working-tree +1 LOC) — `sidebarPaths` array now has **11 entries** including `/inbox-hub/`. Click → `history.back()` when same-origin, else `/dashboard/`. **Included by 17 page templates**.
- `templates/pages/` — **18 subfolders + 8 root files**. Subfolders: agents/, analytics/, audit_log/, auth/, billing/, contacts/, emails/, groups/, inbound_email/, **inbox_hub/** (NEW), kanban/, knowledge/, messaging/, reminders/, settings/, tickets/, users/, voip/. Root: 403.html, api_quickstart.html, calendar.html, dashboard.html, landing.html, login.html, profile.html, register.html.
- `templates/landing/landing_crm.html` (1,393 LOC) — standalone marketing page.
- Email templates (12 files / 6 pairs).

### Profile v2 page (`templates/pages/profile.html`, 444 LOC)

`.pf2-*` namespace. Root `.pf2-shell[data-current-user-id="{{ user.id }}"]` with hero + 3 cards (Personal Information, Work Information, Account & Security). **Inline-edit fields** routed by `data-target` to `PATCH /api/v1/accounts/users/{userId}/` (first_name, last_name, phone) or `PATCH /api/v1/accounts/profiles/me/` (job_title, department, bio); email is `.pf2-row--locked`. **Avatar upload** via `Api.upload('/api/v1/accounts/profiles/upload-avatar/', formData)` (2MB cap, `image/*` MIME check client-side). **No modals** — all editing inline; Enter saves, Esc cancels. **Role badge** picks 1 of 4 variants (`--admin/--manager/--agent/--viewer`) — Team Lead/IT/HR all collapse to `--agent` (info color).

### Reminders v2 page (`templates/pages/reminders/list.html`, 3,263 LOC working tree)

Full split-pane workspace. State object:
```js
state = {page, totalPages, mineOnly, status, priority, quickFilter, search, selectedId, selectedReminder, mode: 'empty'|'view'|'form', formIsEdit, items, checked: Set()}
```

**Mode machine**: `showEmpty()` / `showView()` / `showForm()`. **Global keybinds** are **`N` (new) and `Esc` (back to empty) only** — `Enter` is bound per-input on the two quick-add fields (NOT global). Quick-add Enter on inline row creates+opens detail; Enter in focus-card stays on focus card. **5 group buckets** (`overdue/today/upcoming/done/cancelled`) with chip rail that doubles as quick-filter presets (`overdue/mine-overdue/pending/today/completed-today`). **Workload banner** drives 4 narrative tones from stats. **Bulk bar** with complete/reschedule/cancel; reschedule modal uses `__bulk__` sentinel id. **API endpoints**: `/api/v1/crm/reminders/` (list, create), `/stats/`, `/complete/`, `/reschedule/`, `/cancel/`, `/bulk-action/`, plus form dependencies. **LiveBus subs**: 5 verbs + `live.reconnected`, all debounced 500ms.

### Groups smart member picker (`templates/pages/groups/list.html`, 734 LOC working tree, +88 net)

Client at [groups/list.html:378-387](templates/pages/groups/list.html#L378): `getOtherGroupMemberMap()` returns `{user_id: group_name}` for every user already in any OTHER group. Picker excludes those users unless they're already in the currently-edited group's `selectedUserIds`. Empty states: search empty / all hidden / no users. Legacy multi-group users get `.bg-danger-subtle` row + `<i class="ti ti-alert-triangle"></i> in '<groupname>'` chip. **Footer hint disables Save button** when conflicts are selected.

Server enforcement:
- `UserGroupSerializer.validate_member_ids` ([apps/accounts/serializers.py:298-335](apps/accounts/serializers.py#L298)) — raises `serializers.ValidationError` with a flat string `"A user can only belong to one group at a time. Remove them from the other group first. {display} is already in '{group_name}'; ..."`. **NO `conflicts: [...]` array** — single human string only.
- `UserGroupViewSet.add_members` ([apps/accounts/views.py:474-519](apps/accounts/views.py#L474)) — mirror of the check for the bulk-add action. Returns HTTP 400 with `{"detail": "A user can only belong to one group at a time. {display} is already in '{group_name}'; ..."}`. **Same flat string response shape**, no `conflicts` array.

### Audit log page (`templates/pages/audit_log/list.html`)

Two top-level tabs (`pane-activity` + `pane-inbound-email`). Activity pane Insights redesign: hero with `#statTotal` + stat tiles, side blocks (top contributor + risk signal), full-width heat ribbon (last 7 days), filter chip rail with per-chip counts, dropdown panel (date range / actor multi-select / action multi-select), day-grouped timeline (`.audit-day-events` + `.audit-event`), side drawer with prev/next nav. `dedupTimeline()` merges near-duplicate rows within 2-second window keyed by `(action, object_id)`.

### Kanban board page (`templates/pages/kanban/board.html`)

Filter panel ported from calendar.html. Filter panel teleported to `<body>` at z-index 1085 to escape kanban transform stacking. **SortableJS 1.15.2** for column DnD; `onEnd` posts to `CardPositionViewSet.move/reorder` which pass `actor=request.user, request=request` → ticket status changes route through `apps.tickets.services.change_ticket_status`.

### Settings hub (`templates/pages/settings/tenant.html`, 5,086 lines)

Searchable hub with ~20 panes including Developer & Integrations (apiKeysPane + apiDocsPane). API-keys pane: form + 3-filter table + 3 modals (generate / reveal-once / regenerate / revoke).

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
12. **RateLimitHeadersMiddleware** (`apps.api_keys.middleware` — emits `X-RateLimit-Limit/Remaining/Reset` from `request._kanzan_throttle_info`)
13. MessageMiddleware
14. XFrameOptionsMiddleware

## REST Framework Config (`main/settings/base.py:212-244`)

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

**Only `AuthViewSet` sets `throttle_scope = "auth"`** — `api_heavy`/`webhook` defined but never opted into. `APIKeyRateThrottle` opt-in-free: auto-engages when `request.auth` is an `APIKey`; stashes `(limit, remaining, reset_epoch)` on BOTH `request._kanzan_throttle_info` and `request._request._kanzan_throttle_info` so middleware reads it regardless.

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

27 deps. `django-celery-beat` excluded (Django 6 incompat). **`requirements/prod.txt` has zero extras** — just `-r base.txt`. Root **`requirements.txt`** is **byte-identical** to `requirements/base.txt`.

## Billing Plans

| Plan | Users | Contacts | Tickets/mo | Storage | API | SSO | SLA | VoIP | Call Recording |
|------|-------|----------|-----------|---------|-----|-----|-----|------|----------------|
| Free | 3 | 500 | 100 | 1GB | No | No | No | No | No |
| Pro | 25 | 10K | 5K | 25GB | Yes | No | Yes | Yes | Yes |
| Enterprise | Unlimited | Unlimited | Unlimited | Unlimited | Yes | Yes | Yes | Yes | Yes |

Plan also has: `has_realtime`, `has_custom_roles`, `max_custom_fields`, `max_calls_per_month`, `audit_retention_days` (NULL = unlimited).

## Management Commands (7 total)

```bash
# Tenancy
python manage.py provision_tenant --name "Acme" --slug acme [--domain crm.acme.com]

# Seeding
python manage.py seed_plans                                    # Free/Pro/Enterprise (idempotent)
python manage.py setup_queues --tenant-slug demo               # 4 default queues
python manage.py setup_ticket_statuses --tenant-slug demo      # 5 default statuses
python manage.py backfill_sla_audit [--tenant-slug] [--dry-run]   # baseline SLA audit

# Long-running daemons
python manage.py run_smtp_server                               # kanzan-smtp PM2 process
python manage.py run_ari_listener                              # VoIP Stasis event loop (NOT in PM2)
```

## Environment Variables

### In `.env.example` (16 keys)
`DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DATABASE_URL`, `REDIS_URL`, `BASE_DOMAIN`, `BASE_SCHEME`, `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`, `JWT_SECRET_KEY`, `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `KANZAN_FLOWER_AUTH`.

### Read by `base.py` but NOT in `.env.example` (20 keys)
- **Base:** `BASE_PORT`
- **IMAP:** `IMAP_HOST`, `IMAP_PORT`, `IMAP_USER`, `IMAP_PASSWORD`, `IMAP_MAILBOX`, `IMAP_USE_SSL`, `IMAP_DEFAULT_TENANT_SLUG`
- **SMTP server:** `SMTP_SERVER_HOST`, `SMTP_SERVER_PORT`, `SMTP_SERVER_HOSTNAME`, `SMTP_SERVER_REQUIRE_AUTH`, `SMTP_SERVER_AUTH_USERS` (JSON dict), `SMTP_SERVER_TLS_CERT_FILE`, `SMTP_SERVER_TLS_KEY_FILE`
- **Inbound webhook (unused today):** `INBOUND_EMAIL_WEBHOOK_SECRET`
- **Email:** `EMAIL_BACKEND`, `DEFAULT_FROM_EMAIL`, `EMAIL_TIMEOUT`, `EMAIL_USE_SSL`

> `KANZAN_FLOWER_AUTH` is NOT read by `base.py` — only consumed by `ecosystem.config.js` for Flower's `--basic_auth`.

`main/settings/__init__.py` loads `base.py` then conditionally loads `dev.py` (when `DJANGO_DEBUG=True`) or `prod.py` (with try/except). `pytest.ini` sets `DJANGO_SETTINGS_MODULE=main.settings`.

## Testing

- **Framework:** pytest + pytest-django.
- **Module counts:** **55 root-level** `tests/test_*.py` + **7 app-level** = **62 total**.
- **Config:** `pytest.ini` — `DJANGO_SETTINGS_MODULE=main.settings`, `pythonpath=.`. **No `asyncio_mode`** (defaults to `strict`).
- **Fixtures (`conftest.py`):** **16 factories + 20 fixtures (3 autouse:** `celery_eager`, `free_plan`, `clear_tenant_context`**).** `RoleFactory` declares only 4 traits (admin/manager/agent/viewer) — does not cover `team-lead`/`it`/`hr`; tests fetch `Role.unscoped.get(slug=...)` from the signal-seeded set.
- **Celery:** Eager mode (autouse).
- **API-keys tests** wrap POSTs in `django_capture_on_commit_callbacks(execute=True)` so `transaction.on_commit` callbacks fire under pytest-django's atomic-rollback teardown.
- **`tests/test_inbox_hub.py`** — 13 passing tests covering park/convert/dismiss services + 7 HTTP/RBAC scenarios.

## Recent Migration Highlights

| App | Latest | What it adds |
|-----|--------|--------------|
| **inbox_hub** | **0001_initial** (working tree) | NEW app. 8 models (5 indexes incl. partial SLA index on HubEmail; conditional UniqueConstraints on HubEmailSLA) |
| accounts | **0012_seed_inbox_hub_permissions** (working tree) | Data migration — 12 new permission codenames granted to system roles per the RBAC grid; `role.permissions.add(*perms)` preserves operator customisations |
| accounts | 0011_seed_team_lead_it_hr_roles | Data migration — backfills Team Lead/IT/HR roles |
| accounts | 0010_user_is_service_account | `User.is_service_account` BooleanField |
| accounts | 0009_usergroup | `UserGroup` model |
| accounts | 0008_add_temporary_permissions | `TenantMembership.temporary_permissions` M2M |
| agents | 0006_customagentstatus_…_custom_status | `CustomAgentStatus` + FK |
| analytics | 0003 | Calendar color + end_date |
| **api_keys** | **0002_rename_*_idx** (working tree) | Pure cosmetic index renames |
| api_keys | 0001_initial | `APIKey` model |
| billing | 0002 | VoIP feature flags |
| **comments** | **0010_alter_activitylog_action** (working tree) | Action choices → **34** (+8 `EMAIL_*`) |
| contacts | 0005_widen_phone_field | Wider phone field |
| crm | 0004_reminder_m2m_contacts_tickets | Reminder.contact/ticket FK → M2M |
| **inbound_email** | **0009_alter_inboundemail_status** (working tree) | Adds `Status.PARKED_IN_HUB` |
| inbound_email | 0008_…_imappollstate | IMAPPollState |
| kanban | 0004_board_is_personal | `Board.is_personal` |
| knowledge | 0005_article_allowed_groups | `allowed_groups` M2M |
| messaging | 0002_conversation_source_group | `source_group` FK |
| newsfeed | 0002 | Reactions + reads |
| **notifications** | **0005_alter_notification_type_and_more** (working tree) | NotificationType → **20** (+5 `HUB_EMAIL_*`) |
| **tenants** | **0009_tenantsettings_inbox_hub_enabled** (working tree) | `inbox_hub_enabled` BooleanField (default `False`) |
| tenants | 0008 | Auto-assign toggle |
| **tickets** | **0027_queue_department** (working tree) | `Queue.department` FK to `inbox_hub.Department` (nullable SET_NULL) |
| tickets | 0026_alter_ticketactivity_event | TicketActivity → 27 |
| voip | 0002 | Asterisk SSL config |

## Performance Optimizations

- **Analytics closed-status cache** — per-request `_closed_status_cache` in `DashboardView`.
- **Analytics negative-delta guard** — filters `first_responded_at__gte=F("created_at")`.
- **Kanban N+1 fix** — `BoardDetailSerializer.get_columns` batch-fetches GenericFK content objects.
- **Kanban populate** — subquery `.exclude()`.
- **Comment attachment prefetching** — batch-fetched and set on `_prefetched_attachments`.
- **Contact group bulk add** — set-based batch.
- **Company `contact_count` annotation** — DB level.
- **SLA breach iteration** — `iterator(chunk_size=200)`.
- **First-response race** — atomic UPDATE + WHERE.
- **Lead/health scoring** — pre-fetches signal sets, iterates chunk_size, bulk-updates by score bucket.
- **Reminder overdue task** — `Reminder.unscoped + iterator(chunk_size=200)`, 1-per-day dedup.
- **HubEmailViewSet `get_queryset`** — `select_related("inbound", "contact", "department", "queue", "assignee", "converted_ticket")`.

## Security Hardening

- `IsTenantMember` applied to AttachmentViewSet, BoardViewSet, ColumnViewSet, CardPositionViewSet, ContactGroupViewSet, ConversationViewSet, MessageViewSet, NotificationViewSet, NotificationPreferenceViewSet, QuickNoteViewSet, ReminderViewSet, InboxViewSet, **HubEmailViewSet** — blocks cross-tenant JWT access.
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

## Key Implementation Details

- **Live broadcasts are best-effort + transactional.** `broadcast_live_event` defers via `transaction.on_commit` and swallows exceptions.
- **`effective_role` everywhere.** Exception: `BadgeCountView` uses `membership.role` ([nav/views.py:59](apps/nav/views.py#L59)).
- **`has_effective_permission` honours `temporary_permissions` intersection**.
- **Ticket number per-tenant sequencing** via dedicated `TicketCounter` model (SELECT FOR UPDATE).
- **Signal dedup flag** `_skip_signal_logging`; service-layer functions set this automatically.
- **Kanban drag → ticket service routing** for cross-status drags.
- **`Ticket.save()` auto-populates `company`** from linked Contact's company.
- **`Article.save()` resolves tenant from context** with fallback slug.
- **Session cookie** host-only (Chrome's strict `.localhost` policy).
- **File upload paths** — `tenants/{id}/attachments/YYYY/MM/{filename}`; recordings, inbound similarly.
- **InboundEmail tenant resolution post-parse** — `tenant` nullable.
- **CannedResponse ownership** — creator or Manager+.
- **SavedView default race** — `transaction.atomic() + select_for_update()`.
- **SLA breach flags persisted before notifications** (dedup).
- **Ticket soft delete** — `is_deleted=True`; `?include_deleted=true` shows; POST `restore/` reverses.
- **Ticket watchers** — duplicates return 409.
- **Time tracking** — 1–1440 mins, billable.
- **Macro application** — renders `{{ticket.*}}/{{contact.*}}/{{agent.*}}/{{ticket.queue}}` variables.
- **Knowledge `Article.allowed_groups`** — visibility scope via M2M UserGroup.
- **Kanban `Board.is_personal`** — private to creator.
- **Inbox Hub services broadcast directly** (`broadcast_live_event` itself defers via `on_commit` internally).

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
17. **9 Beat tasks**. `check_overdue_reminders` and `check_sla_breach_warnings` exist but NOT scheduled.
18. **CSS versioning** — `custom-v15.css` (live, 24,622 LOC); `custom.css` (snapshot, NOT loaded).
19. **IMAP "never backfill" safety**.
20. **Tenant primary/accent override** supported. `TenantSettings` defaults `#6366F1`/`#F59E0B`; fallback Crimson Black.
21. **Reminder M2M** — `contacts`/`tickets` are M2Ms.
22. **Knowledge-base task names** — `knowledge_base.*` namespace.
23. **TicketActivity events** — 27 choices.
24. **ActivityLog actions** — **34 choices** (+8 `EMAIL_*` for Inbox Hub).
25. **Temporary role overrides** — use `effective_role`; honour `temporary_permissions` intersection.
26. **API router include count is 23** (22 unique URLConfs).
27. **Frontend URL count is 35** (added `/inbox-hub/`).
28. **91 Django model classes** across 21 apps with `models.py`.
29. **No new hex colour literals in CSS/JS/template rule bodies**.
30. **Use `var(--crm-text-on-primary)`** for text on tenant-themed surfaces.
31. **JS color strings use var() too**.
32. **Hex-alpha concat forbidden** — use `withAlpha(color, percent)`.
33. **Chart.js can't resolve `var()`** — use `cssVar()` + `resolveColor()`.
34. **Live broadcast layer is committed** at 241e407.
35. **Comment broadcasts ignore `is_internal`** — latent info-leak.
36. **TicketPresenceConsumer `presence_list` unimplemented**.
37. **`UserGroup`** — tenant-scoped; "one user per group" enforced.
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
57. **`page_back_button.html` wired into 17 pages.** Hardcoded array now has 11 entries (incl. `/inbox-hub/`).
58. **`requirements.txt` byte-identical to `requirements/base.txt`**.
59. **Logs not rotated.** ~54MB; celery-worker-error 29MB, celery-beat-error 11MB (new offender).
60. **Reminders v2** — Global keybinds `N`/`Esc` only; `Enter` per-input.
61. **api_keys/0002 (index renames)** — generated; uncommitted.
62. **Kanban orphan-card cleanup** — `Ticket.post_delete` hard-deletes `CardPosition`. Tickets app has **11 signal receivers**.
63. **CSS token scales** (`3c99a85`) — no magic numbers in rule bodies.
64. **`theme-check` scans inside `<script>` blocks** (`22f284a`).
65. **Inbox Hub seam is flag-gated and OFF by default.** `tenant.settings.inbox_hub_enabled = True` opts in. Reverting restores legacy behaviour for new mail.
66. **`park_email_in_hub` writes `EMAIL_RECEIVED` ActivityLog + broadcasts `hub_email.created`**. `_post_park_hooks` body still empty.
67. **Inbox Hub permissions enforced as of Phase 1A.** `accounts/0012` seeds 12 codenames. `ACTION_MAP` maps `convert_to_ticket → convert` / `dismiss → dismiss`.
68. **`ActivityLog.Action` 34** (+8 `EMAIL_*`). **`NotificationType` 20** (+5 `HUB_EMAIL_*`). HUB_EMAIL_* NOT yet created by any code path.
69. **`Queue.department` FK opt-in and nullable** — legacy queue behaviour unaffected.
70. **DO NOT set class-level `queryset = Model.objects.all()` on TenantAwareManager viewsets** — evaluates at import time, returns `.none()` forever. Build fresh in `get_queryset()`.
71. **Profile v2 has only 4 role badge variants** — Team Lead/IT/HR collapse to `--agent`.
72. **Sidebar Emails visible**; sidebar avatar image-aware via server-emitted `user.updated` payload.
73. **Groups smart picker** — server response is **flat concatenated string** in `detail`, NOT a structured `{conflicts: [...]}` array.
74. **`docs/README.md:34` stale** — cites `bb36325` / 2026-05-11; reference files say `ea87bb2` / 2026-05-22. All 4 reference files STALE vs working tree.
75. **`.claude/settings.local.json` has stale path** — dbhub MCP DSN says `Kanzan` (actual is `Kanzen`).
76. **Inbox Hub badge IS wired** — `BadgeCountView._inbox_hub_count` exists. **But uses `membership.role`, not `effective_role`** — temp role grants don't shift badge scoping.
77. **Inbox Hub frontend SHIPPED** — `/inbox-hub/` route + 281-LOC template + 666-LOC inbox-hub.js + ~535-LOC CSS section. Docs that say "no frontend yet" are STALE.
78. **Inbox Hub 8-second auto-redirect** after convert — Phase 2 will replace with Undo.
79. **Inbox Hub convert modal stale `<option value="medium">`** — would 400. Assignee dropdown never populated AND payload doesn't include `assignee_id`.
80. **`process_inbound_email` variable-shadowing** — local `settings` rebinds module-level `django.conf.settings`. Sibling `resolve_tenant_from_address` unaffected.
81. **`resolve_tenant_from_address` gained Strategy 4** (working tree) — `IMAP_DEFAULT_TENANT_SLUG` fallback now consulted.
82. **48 `.html` templates / 18 `templates/pages/` subdirs / 14 JS files / 4,734 LOC** — counts shifted +1 for inbox_hub frontend.
83. **`Makefile.logs-django` declared in `.PHONY` but no rule body** — calling errors. Existing log targets: `logs`, `logs-celery`, `logs-all`.
84. **`KANZAN_FLOWER_AUTH` is PM2-only** — `base.py` doesn't read it.

## Documentation

- `/CLAUDE.md` (this file) — day-to-day source of truth.
- `/docs/README.md` — index; **stale pointer** at L34 (says `bb36325`/2026-05-11; reference files say `ea87bb2`/2026-05-22).
- `/docs/architecture.md` — long-form architecture (Version 1.0, **2026-02-06**; STALE — ~4 months out of date).
- `/docs/ui-consistency-audit.md` (211 LOC, 2026-05-22, `2ae0c10`) — findings doc; **most recommendations now shipped**. Its self-referenced "baseline 125" figure outdated — current baseline is **147**.
- `/docs/reference/codebase-inventory.md` — Verified 2026-05-22 at `ea87bb2`. **STALE vs working tree** — does NOT cover Inbox Hub, Profile v2, Reminders v2, Groups smart picker.
- `/docs/reference/api-surface.md` — Same — does NOT cover `/api/v1/inbox-hub/hub-emails/`.
- `/docs/reference/frontend-surface.md` — Same — does NOT cover `templates/pages/inbox_hub/`, `static/js/inbox-hub.js`, Profile v2, Reminders v2.
- `/docs/reference/infra-surface.md` — Same — does NOT cover `apps.inbox_hub` in INSTALLED_APPS or the inbox_hub URL include.
- `/scripts/check_theme.py` + `.theme_baseline.json` — regression guard. Baseline total: **147 hex / 11 files**.
- `README.md` — minimal stub (`# Kanzen`).
