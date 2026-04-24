# Kanzen — Project Intelligence

## Project Overview

Multi-tenant CRM, Ticketing, Knowledge Base, and VoIP SaaS. Django 6.0.2 + DRF 3.16 + Channels 4.2+ + Celery 5.4+. Bootstrap 5.3 front-end with vanilla JS (SIP.js for softphone, TipTap for rich editor). PM2 process management. Row-level multi-tenancy via subdomain routing and thread/context-local tenant binding.

**Port:** 8001 (ASGI via Gunicorn + Uvicorn worker) | **Dev DB:** SQLite | **Prod DB:** PostgreSQL
**Redis:** db3 (cache + cached_db sessions), db4 (Celery broker + django-db result backend), db5 (Channels layer)
**SMTP in-process server:** 2525 (kanzan-smtp PM2 process)
**Flower:** 5556

## Quick Reference

```
Superuser:      admin@epstein.local / Pl@nC-ICT_2024
Django Admin:   http://localhost:8001/admin/

Tenants:
  DPAP:         http://dpap.localhost:8001      (domain: asmra.shop)
    Admin:      admin@dpap.local
    Manager:    kavinkumar291@gmail.com
    Agents:     created@test.com, jeffry@company.com, user@company.com, admin@company.com
    Plan:       Free (active)

  Meeting:      http://meeting.localhost:8001
    Admins:     admin@meeting.local, test@gmail.com
    Agent:      test@yahoo.com

  Debug:        http://debug-test.localhost:8001
    Admin:      debug@test.com

Flower:         http://localhost:5556 (admin:changeme)
API Docs:       http://dpap.localhost:8001/api/docs/
```

## Project Structure

```
/home/kavin/Kanzen/
├── apps/                          # 19 Django apps (+ apps.nav URL-only helper)
│   ├── accounts/                  # Users, RBAC, permissions, invitations, profiles
│   ├── agents/                    # Agent availability + workload tracking
│   ├── analytics/                 # Reports, dashboard widgets, exports
│   ├── attachments/               # File uploads (polymorphic GenericFK)
│   ├── billing/                   # Stripe billing, plans, subscriptions, webhook handlers
│   ├── comments/                  # Comments + ActivityLog (audit trail)
│   ├── contacts/                  # Contacts, companies, groups
│   ├── crm/                       # NEW: Activities, Reminders, lead/account scoring, pipeline forecasting
│   ├── custom_fields/             # EAV custom fields per tenant
│   ├── inbound_email/             # SMTP+IMAP ingestion → tickets; agent inbox workflow
│   ├── kanban/                    # Visual boards, columns, card positions
│   ├── knowledge/                 # KB articles, categories, search, stale-article alerts
│   ├── messaging/                 # Real-time conversations (WebSocket)
│   ├── nav/                       # NEW: URL-only helper (sidebar badge counts API — no AppConfig)
│   ├── newsfeed/                  # NEW: Internal announcements, reactions, read receipts
│   ├── notes/                     # Personal sticky notes
│   ├── notifications/             # In-app + email notifications + WebSocket
│   ├── tenants/                   # Tenant model, middleware, frontend views, frontend_urls
│   ├── tickets/                   # Core ticketing, SLA + business hours, CSAT, pipelines, macros, webhooks
│   └── voip/                      # NEW: Asterisk ARI integration, SIP softphone, call logs, recordings
├── main/                          # Django project root
│   ├── settings/{base,dev,prod}.py
│   ├── celery.py                  # Celery app + queue routing (4 queues)
│   ├── asgi.py                    # ProtocolTypeRouter: HTTP + WebSocket (5 consumer groups)
│   ├── context.py                 # contextvars-based tenant context (async-safe)
│   ├── models.py                  # TimestampedModel, TenantScopedModel
│   ├── managers.py                # TenantAwareManager, SoftDeleteTenantManager
│   └── urls.py                    # /api/v1/ router + /api/docs/ + frontend URL include
├── templates/
│   ├── base.html                  # Layout, toast container, notes panel, softphone (conditional)
│   ├── includes/                  # navbar, sidebar, softphone, messages, kb_sidebar_widget
│   ├── pages/                     # 20+ page folders (see Frontend Routes)
│   ├── knowledge/email/           # article_rejected.{html,txt}
│   ├── notifications/email/       # notification.{html,txt}
│   └── tickets/email/             # ticket_created, reply_notification, csat_survey (html+txt)
├── static/
│   ├── css/custom.css             # ~19K lines (design system + components)
│   └── js/                        # 12 JS modules (api, app, ticket-feed, voip-softphone, …)
├── tests/                         # 50+ pytest modules at project root
├── conftest.py                    # Factories: Tenant, User, Role, Membership, Status, Queue, Ticket
├── pytest.ini                     # DJANGO_SETTINGS_MODULE + test discovery
├── requirements/{base,dev,prod}.txt
├── ecosystem.config.js            # PM2: 5 processes (django, worker, beat, flower, smtp)
├── ecosystem.dev.config.js        # PM2 dev overrides
├── Makefile                       # dev/start/stop/restart/migrate/test/smoke/lint targets
├── docs/architecture.md           # Long-form architecture doc (~950 lines)
├── tmp/emails/                    # Dev email capture (EMAIL_FILE_PATH via filebased backend)
├── logs/                          # PM2 log files (one per process, error+out)
├── media/                         # User-uploaded files (tenants/{tenant_id}/…)
├── db.sqlite3                     # Dev database
├── celerybeat-schedule            # Celery Beat shelve file (not django-celery-beat; pinned off Django 6)
└── .env                           # Environment variables (not committed)
```

## Multi-Tenancy Architecture

### Three-Layer Isolation

1. **TenantMiddleware** (`apps/tenants/middleware.py`): Resolves tenant from subdomain (`{slug}.localhost`) or custom `TenantSettings.domain`. Sets `request.tenant` and binds thread/asyncio context. Exempt paths: `/admin/`, `/static/`, `/api/v1/accounts/auth/`, `/api/v1/billing/plans/`, `/api/v1/billing/webhook/`, `/api/docs/`, `/accounts/`.

2. **TenantAwareManager** (`main/managers.py`): Default `objects` manager auto-filters by `get_current_tenant()`. Returns **empty** queryset when no tenant in context (prevents leakage in admin/Celery). Use `Model.unscoped` for cross-tenant queries.

3. **TenantScopedModel** (`main/models.py`): Base class auto-assigns `tenant` on `save()`. Raises `ValueError` if no tenant context. `SoftDeleteTenantManager` adds `is_deleted=False` filter on top.

### Async-Safe Tenant Context (`main/context.py`)
```python
set_current_tenant(tenant)     # Set in middleware / task
get_current_tenant()           # Used by managers & models
clear_current_tenant()         # Cleanup in finally block
with tenant_context(tenant):   # Context-manager form (preferred for tasks)
    ...
```

Uses `contextvars.ContextVar` — safe across asyncio tasks and Channels consumers.

## Models (~90 classes across 19 apps)

### Base Models (Abstract)
- **TimestampedModel**: UUID PK + `created_at` + `updated_at`
- **TenantScopedModel**: Inherits Timestamped + `tenant` FK + auto-filtering

### Tenant / Accounts

**tenants**: `Tenant` (name, slug, domain, is_active, logo), `TenantSettings` (1:1; auth_method, SSO config, branding, `inbound_email_address`, `business_days` JSON, `business_hours_start/end`, `accent_color`)

**accounts**: `User` (email-based, UUID PK), `Permission` (resource.action codenames), `Role` (hierarchy_level: Admin=10, Manager=20, Agent=30 — **no Viewer**), `Profile` (tenant-specific: job_title, timezone, language, DND, theme, density, date/time format, notification email, signature), `TenantMembership`, `Invitation`

### Tickets — heaviest app (22 classes in `apps/tickets/models.py`)

Core: `Ticket` (soft-delete: `is_deleted`/`deleted_at`/`deleted_by`; CSAT: `csat_rating`/`csat_comment`/`csat_submitted_at`; deal: `ticket_type`/`deal_value`/`expected_close_date`/`probability`/`pipeline_stage`/`account`/`won_at`/`lost_at`; `merged_into`/`needs_kb_article`/`auto_close_task_id`), `TicketStatus` (`pauses_sla` flag, `is_closed`, `is_default`), `TicketCategory`, `Queue`, `TicketCounter` (per-tenant number sequencer), `TicketActivity` (timeline), `TicketAssignment` (audit), `TicketWatcher`, `TicketLink` (directed duplicate/related/blocked_by + circular-dependency guard), `TimeEntry`

SLA + business hours: `SLAPolicy`, `EscalationRule`, `BusinessHours` (per-day JSON schedule + timezone), `PublicHoliday`, `SLAPause` (paused_at/resumed_at — created when entering `pauses_sla` status)

Templates & productivity: `CannedResponse` (variables `{{ticket.*}}`, `{{contact.*}}`, `{{agent.*}}`, usage counter), `SavedView` (personal/shared, is_default), `TicketTemplate`, `Macro` (reusable actions: set_status/set_priority/add_tag; `apply_macro` service), `Webhook` (HMAC SHA-256, auto-disable after 10 failures)

Deals: `Pipeline` (is_default flag), `PipelineStage` (probability, is_won, is_lost)

### Contacts

`Contact` (email unique per tenant, company FK, groups M2M, `email_bouncing` flag set by BounceLog), `Company` (name unique per tenant, `contact_count` annotation), `ContactGroup`

### Inbound Email (2 models, many services)

`InboundEmail` extends `TimestampedModel` (NOT TenantScopedModel — tenant resolved post-parse, nullable FK). Unified model for inbound and outbound (`direction` field). Status: `pending/processing/ticket_created/reply_added/sent/rejected/bounced/failed`. Inbox workflow: `inbox_status` (`pending/linked/actioned/ignored`), `linked_ticket`, `linked_at/by`, `actioned_at/by`, `action_taken` (OPEN/ASSIGN/CLOSE). Message threading: `message_id`, `in_reply_to`, `references`. Idempotency via `idempotency_key = "in:{tenant_id}:{message_id}"`. Sanitized subject (strip `\r`/`\n`).

`BounceLog` — hard-bounce records linked to `InboundEmail` + sender email + reason + optional ticket.

### VoIP (5 models)

`VoIPSettings` (per-tenant singleton: Asterisk host/ARI port/WSS port, SSL toggle, encrypted ARI creds, STUN/TURN servers, default caller ID, PJSIP context, `recording_enabled`/`voicemail_enabled`/`is_active`)
`Extension` (user → SIP endpoint: sip_username globally unique, encrypted sip_password, extension_number, caller_id_name/number, registered_at)
`CallLog` (direction: inbound/outbound/internal; status: ringing/answered/on_hold/completed/failed/missed/busy/no_answer/voicemail; timing: started_at/answered_at/ended_at/duration_seconds/hold_duration_seconds; `asterisk_channel_id` indexed; metadata JSONField; FKs to caller/callee Extension, Contact, Ticket)
`CallRecording` (1:1 CallLog; file upload to `tenants/{tenant_id}/recordings/{YYYY}/{MM}/{uuid}.wav`)
`CallQueue` (ACD strategy: ring_all/round_robin/least_recent/fewest_calls/random; M2M Extension members)

### CRM (2 models — deal fields live on Ticket)

`Activity` (type: call/email/meeting/task; subject, notes, due_at, completed_at, outcome; FKs: ticket, contact, created_by, assigned_to; updates `ticket.last_activity_at` atomically on save)
`Reminder` (formerly `Recall` — renamed in migration 0003; priority: low/medium/high/urgent; `scheduled_at`, `completed_at`, `cancelled_at`; custom QuerySet: `.overdue()`/`.pending()`/`.for_user()`; methods: `mark_completed()`, `mark_cancelled()`, `reschedule()`)

### Newsfeed (3 models)

`NewsPost` (title, content, category: announcement/update/celebration/incident/general, `is_pinned`, `is_published`, `is_urgent`, emoji, `expires_at`; pinned first, then newest)
`NewsPostReaction` (emoji choices: thumbs_up/celebration/heart/rocket/eyes/hundred — one per user per post)
`NewsPostRead` (read-tracking; row-existence = read flag; **not** tenant-scoped)

### Knowledge Base

`Category` (slug, ordering), `Article` (draft/published, category FK, author, tags, view_count, pinned, file attachment support). Plus KB notification / vote / feedback models used by `kb-stale-alert` and `kb-gap-digest` Beat tasks.

### Kanban

`Board` (resource_type: TICKET/DEAL), `Column` (status mapping, WIP limit), `CardPosition` (polymorphic GenericFK, ordered, tenant-scoped)

### Comments / Activity / Messaging / Notifications

**comments**: `Comment` (polymorphic GenericFK, threaded via parent), `Mention`, `ActivityLog` (immutable audit trail with diffs+IP)
**messaging**: `Conversation` (direct/group/ticket), `ConversationParticipant` (last_read_at, is_muted), `Message` (threaded, mentions M2M)
**notifications**: `Notification` (9 types, is_read), `NotificationPreference` (per user+tenant+type; in_app + email channels)

### Billing / Analytics / Agents / Custom Fields / Attachments / Notes

**billing**: `Plan` (Free/Pro/Enterprise, limits + feature flags incl. `has_voip`), `Subscription` (1:1 Tenant, Stripe sync), `Invoice`, `UsageTracker` (period counters incl. `calls_made` for VoIP)
**analytics**: `ReportDefinition`, `DashboardWidget`, `ExportJob` (Celery-backed CSV/XLSX/PDF)
**agents**: `AgentAvailability` (online/away/busy/offline, capacity, `auto_away_outside_hours`)
**custom_fields**: `CustomFieldDefinition` (8 types × 3 modules, role visibility), `CustomFieldValue` (EAV: value_text/number/date/bool)
**attachments**: `Attachment` (polymorphic GenericFK, tenant-isolated storage)
**notes**: `QuickNote` (colors: yellow/blue/green/pink/purple/orange, pinning)

## Polymorphic (GenericForeignKey) Models
`Comment`, `ActivityLog`, `Attachment`, `CustomFieldValue`, `CardPosition` — all use `content_type` FK + `object_id` UUID.

## Role-Based Access Control

**Hierarchy:** Admin(10) ≤ Manager(20) ≤ Agent(30) *(no Viewer)*

- `is_admin`: `hierarchy_level ≤ 10`
- `is_admin_or_manager`: `≤ 20` (context processor injects into every template)
- `is_agent_or_above`: `≤ 30`
- Agent restriction (`level > 20`): sees only own tickets, linked contacts, filtered kanban cards, own reminders/activities
- Base permission: `IsTenantMember` (`accounts/permissions.py`) — blocks cross-tenant JWT access
- Row-level filtering in: `TicketViewSet`, `ContactViewSet`, `ReminderViewSet`, `ActivityViewSet`, `BoardDetailSerializer`, analytics services
- `_role_required(20)` decorator gates admin/manager frontend pages (settings, users, billing)
- ACTION_MAP covers: contact groups `add_contacts`/`remove_contacts`, kanban `move`/`reorder`/`populate`, watchers `watch`/`remove_watcher`, time `time_entries`/`time_summary`, templates `use`/`test`/`reset_failures`, macros `apply-macro`, tickets `merge`/`split`/`restore`/`send-email`/`link-email`

## Signals

### Tenant (`apps/tenants/signals.py`)
- `Tenant.post_save` → `create_tenant_settings`, `create_default_roles` (Admin/Manager/Agent with hierarchy_level)

### Accounts (`apps/accounts/signals.py`)
- `TenantMembership.post_save` → `create_profile_on_membership`

### Tickets (`apps/tickets/signals.py`) — 8 handlers
- `Ticket.pre_save` → `handle_ticket_status_change` (sets resolved_at/closed_at, stashes old values)
- `Ticket.post_save` → `fire_ticket_created_signal` + `fire_ticket_assigned_signal` (emit custom signals → webhooks)
- `Ticket.post_save` → `log_ticket_activity` (writes ActivityLog; 2-second dedup window; skips if `_skip_signal_logging` flag set)
- `Ticket.post_save` → `sync_kanban_card_on_status_change`, `create_kanban_card_on_ticket_save`
- `Ticket.post_save` → `handle_sla_pause_on_status_change` / `_resume_sla_pause` (creates/closes `SLAPause` when entering/leaving a `pauses_sla` status; shifts deadlines on resume)
- `Ticket.post_save` → `check_kb_article_coverage`, `propagate_sla_policy_change`

### Custom Fields (`apps/custom_fields/signals.py`)
- `Ticket.post_save`/`Contact.post_save` → sync `CustomFieldValue` from JSON `custom_data`

### VoIP (`apps/voip/signals.py`)
- `CallLog.post_save` on terminal state → writes `TicketActivity` + `comments.ActivityLog` + queues `process_call_recording` when `recording_enabled`

## Dual-Write Logging

**Two parallel log systems:**
1. **TicketActivity** (human-readable timeline) — endpoint: `/api/v1/tickets/tickets/{id}/timeline/`
2. **ActivityLog** (polymorphic audit trail with diffs+IP) — endpoint: `/api/v1/tickets/tickets/{id}/activity/`

**Dedup:** ViewSet sets `instance._skip_signal_logging = True` before save; signal checks flag. Use `serializer.instance` (not `self.get_object()`) in `perform_update` so the flag persists. 2-sec window in signal.

**Service layer** (`apps/tickets/services.py`): `create_ticket_activity`, `assign_ticket`, `transition_ticket_status`, `change_ticket_priority`, `log_ticket_comment`, `close_ticket`, `escalate_ticket`, `merge_tickets`, `split_ticket`, `bulk_update_tickets`, `apply_macro`, `record_first_response`, `transition_pipeline_stage`, `initialize_sla`, `broadcast_ticket_event` — all write to BOTH logs atomically. Broadcast defers WebSocket push to `transaction.on_commit()`.

**Webhook service** (`apps/tickets/webhook_service.py`): `deliver_webhook` signs with HMAC SHA-256 (`X-Webhook-Signature`), 10s timeout, auto-disable at 10 failures. `fire_webhooks(tenant, event_type, data)` dispatches async via Celery. Events: `ticket.created/updated/assigned/closed/reopened/comment`, `sla.breached`, `ticket.escalated`.

**Transaction safety:** Notifications defer WebSocket pushes and email task queuing to `transaction.on_commit()` to avoid orphaned tasks on rollback.

## SLA + Business Hours (`apps/tickets/sla.py`)

Business-hours-aware calculator — single breach-detection entry point `get_effective_elapsed_minutes()`:
- Resolves per-tenant schedule via `BusinessHours` model (JSON per-day open/close + IANA timezone) or legacy `TenantSettings` flat fields
- Skips `PublicHoliday` dates entirely
- Subtracts total pause duration from `SLAPause` records (pause minutes counted in business-hour terms)
- Helpers: `_count_business_minutes()`, `_add_business_minutes()` — day-skip, holiday-skip, hour-windowing
- `initialize_sla(ticket)` service seeds `response_deadline` and `resolution_deadline`
- `_check_first_response_breach` uses atomic UPDATE+WHERE (not save) to avoid races with concurrent responses

## Inbound / Outbound Email

### Inbound (`apps/inbound_email/`)
- **In-process SMTP server** (`smtp_server.py`) via `aiosmtpd`, launched by `run_smtp_server` management command (PM2 process `kanzan-smtp`). Not an open relay — validates RCPT against active tenants, rejects unknown with 550. Optional STARTTLS and LOGIN/PLAIN AUTH.
- **IMAP poller** (`imap_poller.py`) — shared Gmail-style mailbox; `poll_once()` fetches UNSEEN, creates `InboundEmail`, marks SEEN. Driven by `fetch_inbound_emails_task` (Celery Beat, every 60s). Disabled when `IMAP_HOST` is blank.
- **Tenant resolution** — 3 patterns via `resolve_tenant_from_address`: plus-addressing (`support+{slug}@...`), subdomain routing, custom `TenantSettings.inbound_email_address`. Fallback to `IMAP_DEFAULT_TENANT_SLUG` if configured.
- **Filters** (`filters.py`) run BEFORE tenant resolution (cheap): loop detection (sender == `DEFAULT_FROM_EMAIL`), noreply senders, RFC 3834 Auto-Submitted / Precedence: bulk/junk/list, subject patterns. `classify_email()` returns bounce / auto_reply / loop. Bounces write `BounceLog` and flip `Contact.email_bouncing=True`.
- **Threading** (`threading.py`) — `find_existing_ticket` uses 3-tier priority: In-Reply-To → References (reversed, most-recent first) → subject `[#N]` regex. All queries tenant-scoped. Outbound: `build_thread_headers(tenant, ticket, new_message_id)` reads last 10 related InboundEmails for Message-ID chain.
- **Processing pipeline** (Celery `process_inbound_email_task`, max_retries=3, acks_late): parse → filter → resolve tenant → idempotency check → find/create contact → find existing ticket OR create new + init SLA + auto-tag "email" → attach files (stored under `inbound_emails/{pk}/`) → queue confirmation email via `transaction.on_commit()`.
- **Agent inbox workflow** (`inbox_services.py`): `link_email_to_ticket`, `action_email` (OPEN/ASSIGN/CLOSE), `ignore_email`. Mutations atomic; `linked_at/by`, `actioned_at/by` immutable once set.
- **Utils** (`utils.py`): `normalize_message_id`, `normalize_references`, `extract_header` (handles RFC 2822 folding), `parse_sender`, `strip_quoted_reply` (Gmail/Outlook/Apple Mail quoted blocks).

### Outbound (`apps/tickets/email_service.py`)
- `send_ticket_email()` is the single entry point.
- RFC-compliant Message-IDs; sets In-Reply-To, References, Reply-To.
- Persists an OUTBOUND `InboundEmail` record so future replies can be threaded via Message-ID lookup.
- Dev default backend: `django.core.mail.backends.filebased.EmailBackend` → writes to `tmp/emails/`. Prod: SMTP via `EMAIL_HOST/PORT/USER/PASSWORD/USE_TLS`.
- Legacy wrappers: `send_ticket_reply_email`, `send_ticket_created_email`, `send_csat_survey_email` (all dispatched async via `send_ticket_*` Celery tasks).

## VoIP

**Architecture:** Asterisk/FreePBX → ARI (REST + WebSocket Stasis events). Django app wraps ARI, exposes SIP credentials to browser softphone (SIP.js over WSS), persists `CallLog`/`CallRecording`, links to CRM (`Contact`, `Ticket`).

- **`ari_client.py`** — `ARIClient` (async httpx): `originate/hangup/hold/unhold/mute/unmute/redirect/create_bridge/add_channel_to_bridge/start_recording/stop_recording/get_recording_file/get_channel`. `ARIEventListener` connects to `ws(s)://host:port/ari/events?app=kanzan-voip&subscribeAll=true`, exponential reconnect 1–30s.
- **`services.py`** — Sync wrappers around async ARI: `originate_call`, `hangup_call`, `toggle_hold`, `transfer_call` (blind), `process_ari_event` (dispatches ChannelStateChange, Hangup, Destroyed, Hold, Unhold to CallLog updates), `_broadcast_call_event` (group `voip_{tenant_id}`). Billing: `check_call_limit` / `increment_call_usage` against `Plan.has_voip` + `UsageTracker.calls_made`.
- **`consumers.py`** — `CallEventConsumer` (AsyncJsonWebsocketConsumer): `ws/voip/events/`; emits `call_ringing/answered/ended/hold` to browser softphone.
- **Management commands** — `run_ari_listener`: long-running async listener for all active tenants. Spawns one `ARIEventListener` per tenant concurrently; on event → `sync_call_state.delay(channel_id, event)`. Graceful SIGINT/SIGTERM. Sleeps 60s when no active tenants.
- **API** under `/api/v1/voip/`: `settings/` (singleton, admin-only), `extensions/` (CRUD), `calls/` (list/detail + `active_calls`/`call_stats`), `calls/initiate|{id}/hold|{id}/transfer|{id}/hangup/`, `sip-credentials/` (session-authed, returns SIP URI + password + WSS URL + STUN/TURN), `recordings/{id}/` (FileResponse), `queues/` (admin-only CRUD).
- **Softphone** — `templates/includes/softphone.html` + `static/js/voip-softphone.js` using **SIP.js 0.21.2** (CDN, loaded conditionally via `voip_enabled` context var). Dial pad, DTMF, mute/hold/transfer/hangup, incoming-call modal, in-call DTMF overlay. Real-time state sync via `CallEventConsumer`.

## API Architecture

### Authentication
- **API:** JWT (SimpleJWT) — 15min access, 7-day refresh, rotate + blacklist
- **Frontend:** Session auth (Redis-backed cached_db, wildcard subdomain cookie)
- **SSO:** django-allauth (Google, Microsoft, OpenID Connect)

### `/api/v1/` Endpoint Map (from `main/urls.py`)
```
/tenants/            TenantViewSet, TenantSettingsViewSet (singleton)
/accounts/           AuthViewSet (throttle_scope="auth"), UserViewSet, RoleViewSet, ProfileViewSet, InvitationViewSet, MembershipViewSet
/tickets/            TicketViewSet + 20+ custom actions (assign, close, change-status, change-stage, escalate, comments, activity, timeline, restore, watch, watchers, remove_watcher, time-entries, time-summary, merge, split, apply-macro, send-email, link-email, bulk-action, mark-all-read), TicketStatusViewSet, QueueViewSet, TicketCategoryViewSet, SLAPolicyViewSet, EscalationRuleViewSet, CannedResponseViewSet, MacroViewSet, SavedViewViewSet, BusinessHoursViewSet, PublicHolidayViewSet, TicketTemplateViewSet (+use), WebhookViewSet (+test/reset-failures), CSATSubmitView (public, no auth)
/contacts/           ContactViewSet, CompanyViewSet (annotates contact_count), ContactGroupViewSet
/billing/            PlanViewSet, SubscriptionViewSet (singleton +cancel/reactivate), InvoiceViewSet, UsageViewSet (singleton), checkout, webhook (CSRF-exempt)
/kanban/             BoardViewSet (+detail), ColumnViewSet, CardPositionViewSet (+move/reorder)
/comments/           CommentViewSet, ActivityLogViewSet (read-only)
/messaging/          ConversationViewSet, MessageViewSet (nested)
/notifications/      NotificationViewSet (+mark_read, unread_count), NotificationPreferenceViewSet
/attachments/        AttachmentViewSet (multipart upload, cross-tenant validated)
/analytics/          DashboardView, ReportDefinitionViewSet, DashboardWidgetViewSet, ExportJobViewSet
/agents/             AgentAvailabilityViewSet
/custom-fields/      CustomFieldDefinitionViewSet, CustomFieldValueViewSet
/knowledge/          CategoryViewSet, ArticleViewSet, search + voting + feedback
/notes/              QuickNoteViewSet
/inbound-email/      InboundEmailViewSet (read-only log) + Inbox endpoints (link/action/ignore)
/emails/             (alias mount of inbound_email.api_urls with namespace="emails_api")
/crm/                ActivityViewSet (+my-tasks), ReminderViewSet (+overdue/stats/bulk-action/complete/cancel/reschedule), PipelineForecastView
/nav/                BadgeCountView (GET /badge-counts/)
/newsfeed/           NewsPostViewSet (+react/mark-read/mark-all-read/unread-count)
/voip/               VoIPSettingsViewSet, ExtensionViewSet, CallLogViewSet (+active_calls, call_stats), InitiateCallView, CallHoldView, CallTransferView, CallHangupView, SIPCredentialsView, CallRecordingDownloadView, CallQueueViewSet
```

**Non-HTTP inbound channel:** the `kanzan-smtp` PM2 process accepts mail on `SMTP_SERVER_HOST:SMTP_SERVER_PORT` (default `0.0.0.0:2525`) and feeds the same `InboundEmail` + Celery pipeline.

**Docs:** `/api/docs/` (Swagger UI), `/api/schema/` (OpenAPI 3.0 JSON).

### REST Framework Config
- Pagination: PageNumberPagination, PAGE_SIZE=50
- Filtering: DjangoFilterBackend + SearchFilter + OrderingFilter
- Throttle rates: auth=10/min, api_default=200/min, api_heavy=30/min, webhook=60/min
- Renderers: JSON + BrowsableAPI

### Frontend Routes (`apps/tenants/frontend_urls.py`) — 28 total
```
/                             landing_page
/login/                       login_page
/register/                    register_page
/logout/                      logout_page
/dashboard/                   dashboard_page
/tickets/                     ticket_list_page
/tickets/new/                 ticket_create_page
/tickets/<number>/            ticket_detail_page
/contacts/                    contact_list_page
/contacts/create/             contact_create_page
/contacts/<contact_id>/       contact_detail_page
/calendar/                    calendar_page
/kanban/                      kanban_page
/messaging/                   messaging_page
/analytics/                   analytics_page
/users/                       users_page            @_role_required(20)
/settings/                    settings_page         @_role_required(20)
/billing/                     billing_page          @_role_required(20)
/agents/                      agents_page
/emails/                      emails_page           (outbound email log)
/knowledge/                   knowledge_list_page
/knowledge/<slug>/            knowledge_article_page
/profile/                     profile_page
/inbound-email/               inbound_email_page    (agent inbox)
/reminders/                   reminders_page        (NEW)
/audit-log/                   audit_log_page        (NEW)
/calls/                       calls_page            (NEW — VoIP call history)
```

## WebSocket Endpoints (5 total, wired in `main/asgi.py`)

Stack: `AllowedHostsOriginValidator` → `AuthMiddlewareStack` → `WebSocketTenantMiddleware` → `URLRouter(messaging_ws + notification_ws + ticket_ws + voip_ws)`

1. **Chat:** `ws/messaging/{conversation_id}/` → `ChatConsumer`. Actions: `send_message`, `typing`, `mark_read`. Group: `chat_{conversation_id}`. Rate limits: MAX_MESSAGE_LENGTH=10KB, MAX_MESSAGES_PER_SECOND=5, TYPING_COOLDOWN=2s.
2. **Notifications:** `ws/notifications/` → `NotificationConsumer`. Group: `notifications_{user_id}`.
3. **Ticket Presence:** `ws/tickets/{ticket_id}/presence/` → `TicketPresenceConsumer`. Events: `agent_joined`, `agent_left`, `presence_list`. Group: `ticket_{ticket_id}_presence`.
4. **Ticket Feed:** `ws/tickets/feed/` → `TicketListConsumer`. Events: `ticket_created/updated/assigned/closed/deleted`. Group: `ticket_feed_{tenant_id}`.
5. **VoIP:** `ws/voip/events/` → `CallEventConsumer`. Events: `call_ringing/answered/ended/hold`. Group: `voip_{tenant_id}`.

All consumers verify tenant membership from scope; ChatConsumer also validates participant membership.

## Celery Tasks & Beat Schedule

### Queue Routing (`main/celery.py`)
```
apps.billing.tasks.*                              → kanzan_webhooks
apps.notifications.tasks.send_email_*             → kanzan_email
apps.notifications.tasks.send_notification_email  → kanzan_email
apps.inbound_email.tasks.*                        → kanzan_email
apps.tickets.tasks.send_ticket_*                  → kanzan_email
apps.voip.tasks.*                                 → kanzan_voip
*                                                 → kanzan_default
```

### Beat Schedule (9 tasks — `main/settings/base.py` CELERY_BEAT_SCHEDULE)
| Task | Interval |
|------|----------|
| `apps.tickets.tasks.check_sla_breaches` | 120s |
| `apps.notifications.tasks.cleanup_old_notifications` | 86400s (daily) |
| `apps.tickets.tasks.check_overdue_tickets` | 900s (15m) |
| `apps.crm.tasks.calculate_lead_scores` | 86400s (daily) |
| `apps.crm.tasks.calculate_account_health_scores` | 86400s (daily) |
| `knowledge_base.alert_stale_articles` | daily 08:00 (crontab) |
| `knowledge_base.send_gap_digest` | Monday 09:00 (crontab) |
| `apps.voip.tasks.cleanup_stale_calls` | 3600s (hourly) |
| `apps.inbound_email.tasks.fetch_inbound_emails_task` | 60s |

> Celery Beat uses the **built-in shelve scheduler** (`celerybeat-schedule` file at repo root). `django-celery-beat` was removed — incompatible with Django 6.0.

### Celery Task Inventory
- **notifications**: `send_notification_email` (retries=3, acks_late), `cleanup_old_notifications` (batch 1000)
- **analytics**: `process_export_job` (CSV/XLSX; openpyxl optional with CSV fallback)
- **inbound_email**: `fetch_inbound_emails_task` (calls IMAP `poll_once()`), `process_inbound_email_task` (retries=3, acks_late)
- **tickets**: `check_sla_breaches` (iterator chunk_size=200, dedup escalation rules, persists breach flag before notify), `check_overdue_tickets` (daily dedup per ticket), `send_ticket_reply_email_task`, `send_ticket_created_email_task`, `send_ticket_email_task`, `auto_close_ticket`, `send_csat_survey_email`, `deliver_webhook_task`, `check_sla_breach_warnings`, `propagate_sla_policy_change_task`
- **voip**: `process_call_recording` (retries=3), `cleanup_stale_calls`, `sync_call_state`
- **crm**: `check_overdue_reminders` (15-min; escalates to managers >24h), `calculate_lead_scores`, `calculate_account_health_scores`

## PM2 Processes (`ecosystem.config.js`) — 5 total

| Name | Script | Purpose | Memory |
|------|--------|---------|--------|
| `kanzan-django` | Gunicorn + Uvicorn worker (w=2, port 8001, timeout 120) | HTTP + WebSocket (ASGI) | 2GB |
| `kanzan-celery-worker` | `celery -A main worker -Q kanzan_default,kanzan_email,kanzan_webhooks -c 4 --pool prefork --max-tasks-per-child=200` | Background jobs | 2GB |
| `kanzan-celery-beat` | `celery -A main beat` | Periodic scheduler | 512MB |
| `kanzan-flower` | `celery -A main flower --port=5556 --url_prefix=flower --basic_auth=$KANZAN_FLOWER_AUTH` | Monitoring dashboard | 512MB |
| `kanzan-smtp` | `python manage.py run_smtp_server` | In-process SMTP (port 2525) | 512MB |

> Note: the worker's `-Q` list is `kanzan_default,kanzan_email,kanzan_webhooks`. The `kanzan_voip` queue is defined in `main/celery.py` routes, but VoIP tasks only run when `kanzan_voip` is added to `-Q` or a dedicated VoIP worker is started. `run_ari_listener` is not in PM2 by default.

## Frontend Architecture

### JavaScript (`static/js/`, 12 modules)
- **api.js** — Central API client (CSRF from cookie, session credentials, JSON + multipart). Methods: `get/post/patch/put/delete/upload`.
- **app.js** — Global init: auto-dismiss alerts (5s), notification WebSocket, `Toast.{success,error,warning,info}`, cross-page toasts via sessionStorage, date/time localization per user prefs.
- **ticket-feed.js** — WebSocket `ws/tickets/feed/`. Auto-connects via `data-ticket-feed` attribute. Toasts + "new tickets" banner + row pulse. Reconnect with exponential backoff (max 10 attempts, 30s cap).
- **voip-softphone.js** — SIP.js 0.21.2 (CDN) + `CallEventConsumer`. Dial pad, DTMF, mute/hold/transfer/hangup, incoming-call modal.
- **notes-panel.js** — Quick notes CRUD (colors, pinning, localStorage).
- **theme.js** — light/dark/system.
- **agent-availability.js** — Status toggle + persistence.
- **command-palette.js** — Cmd+K modal: static pages, dynamic search, actions.
- **custom-select.js** — Custom dropdown component.
- **rich-editor.js** — TipTap wrapper for comments/articles.
- **keyboard-shortcuts.js** — Global hotkeys: j/k navigate, Enter open, Esc deselect; a/s/x row actions; Ctrl+K palette; c new ticket; ? help; g d/t/c/b go-to. `.keyboard-selected` class.

### CSS (`static/css/custom.css` — ~19K lines)
- Design system with CSS custom properties, dark theme, blue primary `#2563EB`
- Components: sidebar (fixed 272px), stat cards with left accent, soft badges, kanban drag-and-drop, chat bubbles, timeline dots, toast notifications, notes panel, knowledge base, calendar, **softphone widget (~L18984)**, **audit log tabs/stats (~L18054)**, command palette (~L13612), quick notes (~L17040)
- Font: Inter, 0.875rem base; mobile collapses sidebar <992px

### Templates
- `templates/base.html` — layout + toast container + quick-notes panel + **softphone include (conditional on `voip_enabled`)** + DOMPurify CDN + SIP.js CDN (conditional)
- `templates/includes/` — `navbar.html`, `sidebar.html` (sections: Overview, Support, CRM, Activities), `softphone.html`, `messages.html`, `kb_sidebar_widget.html`
- `templates/pages/` — 20+ folders: agents, analytics, audit_log (NEW), billing, calendar.html, contacts, dashboard.html, emails (NEW), inbound_email (NEW), kanban, knowledge, landing.html, login.html, messaging, profile.html, register.html, reminders (NEW), settings, tickets, users, voip (NEW)
- Email templates: `tickets/email/{ticket_created,reply_notification,csat_survey}.{html,txt}`, `notifications/email/notification.{html,txt}`, `knowledge/email/article_rejected.{html,txt}`

### Context Processor (`apps/tenants/context_processors.py`)
Injects into every template: `tenant`, `membership`, `user_role`, `is_admin`, `is_admin_or_manager`, `is_agent_or_above`, **`voip_enabled`** (controls softphone inclusion), `BASE_URL`.

## Middleware Stack (12 layers)
1. SecurityMiddleware
2. WhiteNoiseMiddleware
3. SessionMiddleware
4. CorsMiddleware
5. CommonMiddleware
6. CsrfViewMiddleware
7. AuthenticationMiddleware
8. AccountMiddleware (allauth)
9. **TenantMiddleware** (tenant resolution + thread/async context)
10. **SubscriptionMiddleware** (billing enforcement)
11. MessageMiddleware
12. XFrameOptionsMiddleware

## Third-Party Integrations

| Integration | Purpose | Config |
|-------------|---------|--------|
| Stripe | Payments, subscriptions | `STRIPE_SECRET_KEY/PUBLISHABLE_KEY/WEBHOOK_SECRET` |
| django-allauth | OAuth2 SSO (Google, Microsoft, OIDC) | `ACCOUNT_LOGIN_METHODS={"email"}` |
| Channels + channels-redis | WebSocket real-time | Redis db5, prefix `kanzan:channels` |
| django-redis | Cache + sessions | Redis db3, prefix `kanzan`, cached_db session engine |
| Celery + Redis | Background tasks | Redis db4 broker, django-db results, 4 queues |
| DRF-Spectacular | OpenAPI 3.0 docs | `/api/docs/` |
| SimpleJWT | JWT auth | 15m access, 7d refresh, rotate+blacklist |
| django-filter | API filtering | DjangoFilterBackend + SearchFilter + OrderingFilter |
| WhiteNoise | Static serving | CompressedManifestStaticFilesStorage in prod |
| python-magic | MIME detection | Avatar/logo/attachment uploads |
| mammoth | `.docx` → HTML | Knowledge base |
| aiosmtpd | In-process SMTP server | Inbound email |
| httpx | Async HTTP | Asterisk ARI client |
| websockets | WebSocket client | ARI Stasis events |
| SIP.js (CDN) | Browser SIP/WebRTC | VoIP softphone |
| Jazzmin | Admin theme | Custom sidebar + 24+ model icons |
| Flower | Celery monitoring | Port 5556 |

## Billing Plans

| Plan | Users | Contacts | Tickets/mo | Storage | API | SSO | SLA | VoIP |
|------|-------|----------|-----------|---------|-----|-----|-----|------|
| Free | 3 | 500 | 100 | 1GB | No | No | No | No |
| Pro | 25 | 10K | 5K | 25GB | Yes | No | Yes | Yes |
| Enterprise | Unlimited | Unlimited | Unlimited | Unlimited | Yes | Yes | Yes | Yes |

## Management Commands

```bash
# Tenancy
python manage.py provision_tenant --name "Acme" --slug acme [--domain crm.acme.com]

# Seeding
python manage.py seed_plans                                    # Free/Pro/Enterprise
python manage.py setup_queues --tenant-slug demo               # 4 default queues
python manage.py setup_ticket_statuses --tenant-slug demo      # 5 default statuses
python manage.py backfill_sla_audit                            # baseline SLA audit for in-flight tickets

# Long-running daemons (run under PM2)
python manage.py run_smtp_server                               # kanzan-smtp PM2 process
python manage.py run_ari_listener                              # VoIP Stasis event loop (NOT in PM2 by default — start separately if VoIP is live)
```

## Environment Variables

### In `.env.example`
`DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DATABASE_URL`, `REDIS_URL`, `BASE_DOMAIN`, `BASE_SCHEME`, `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`, `JWT_SECRET_KEY`, `EMAIL_HOST/PORT/USER/PASSWORD/USE_TLS`, `KANZAN_FLOWER_AUTH`

### Not in `.env.example` but read by `base.py`
- **IMAP:** `IMAP_HOST`, `IMAP_PORT`, `IMAP_USER`, `IMAP_PASSWORD`, `IMAP_MAILBOX`, `IMAP_USE_SSL`, `IMAP_DEFAULT_TENANT_SLUG`
- **SMTP server:** `SMTP_SERVER_HOST`, `SMTP_SERVER_PORT`, `SMTP_SERVER_HOSTNAME`, `SMTP_SERVER_REQUIRE_AUTH`, `SMTP_SERVER_AUTH_USERS` (JSON dict), `SMTP_SERVER_TLS_CERT_FILE`, `SMTP_SERVER_TLS_KEY_FILE`
- **Inbound webhook (unused today):** `INBOUND_EMAIL_WEBHOOK_SECRET`
- **Email:** `EMAIL_BACKEND`, `DEFAULT_FROM_EMAIL`, `EMAIL_TIMEOUT`, `EMAIL_USE_SSL`

## Testing

### Infrastructure
- **Framework:** pytest + pytest-django
- **Config:** `pytest.ini` (project root) with DJANGO_SETTINGS_MODULE
- **Fixtures:** `conftest.py` — Tenant, User, Role, Membership, TicketStatus, Queue, Ticket factories (`factory_boy` with `skip_postgeneration_save = True`)
- **Celery:** Eager mode (tasks execute synchronously)

### Test Suite (~50 modules in `tests/` + app-level tests)
Key groupings:
- **Ticket lifecycle & SLA:** test_tickets, test_ticket_creation, test_ticket_lifecycle, test_ticket_assignment, test_ticket_split, test_ticket_linking, test_ticket_improvements, test_ticket_presence, test_first_response, test_closure, test_phase3_resolution, test_phase4_closure, test_pre_wait_status, test_auto_transition_toggle, test_recalls (Reminder), test_macros, test_csat, test_badges, test_sla, test_sla_pause, test_sla_business_hours, test_sla_escalation_extension, test_sla_audit_logging
- **Email:** test_email, test_email_inbound, test_email_outbound, test_inbound_email, test_outbound_email, test_bounce_handling
- **Security & tenancy:** test_auth, test_auth_rbac, test_access_control, test_critical_security, test_security, test_multitenancy, test_tenant_isolation, test_comment_visibility, test_contact_context
- **Other:** test_kanban, test_billing, test_billing_limits, test_api_plan_enforcement, test_plan_limits, test_comments, test_notifications, test_contacts, test_custom_fields, test_kb, test_knowledge_base, test_audit, test_edge_cases, test_crm
- **App-level:** `apps/tickets/tests/{test_creation,test_escalation}.py`, `apps/knowledge/tests/test_kb_gap_fill.py`

## Performance Optimizations

- **Analytics closed-status cache** — per-request `_closed_status_cache` in `DashboardView` avoids repeated DB lookups across `get_ticket_stats`/`get_agent_performance`/`get_due_today`/`get_overdue_tickets`.
- **Kanban N+1 fix** — `BoardDetailSerializer.get_columns` batch-fetches GenericFK content objects grouped by content_type; Tickets pre-select `status` and `assignee`.
- **Kanban populate** — `populate_board_from_tickets` uses subquery `.exclude()` instead of loading all ticket IDs.
- **Comment attachment prefetching** — ticket detail batch-fetches and sets `_prefetched_attachments`.
- **Contact group bulk add** — set-based batch (one query) instead of per-contact `exists()` check.
- **Company `contact_count` annotation** — at DB level; `ContactGroupSerializer` caps contacts at 50.
- **Message reply count** — annotated via `Count("replies")`.
- **SLA breach iteration** — `iterator(chunk_size=200)` to bound memory.
- **First-response race** — atomic UPDATE + WHERE filter (no save()).
- **Bulk ops** — `bulk_update_tickets` handles failures independently per operation.

## Security Hardening

- `IsTenantMember` applied to AttachmentViewSet, BoardViewSet, ColumnViewSet, CardPositionViewSet, ContactGroupViewSet, ConversationViewSet, MessageViewSet, NotificationViewSet, NotificationPreferenceViewSet, QuickNoteViewSet — blocks cross-tenant JWT access.
- ChatConsumer rate limits (10KB msg, 5/sec, 2s typing cooldown) and tenant-from-scope verification. TicketPresenceConsumer, TicketListConsumer, CallEventConsumer all verify tenant membership.
- **Webhook `secret`** write-only in serializer responses; HMAC SHA-256 signing; auto-disable at 10 consecutive failures.
- **XSS prevention** — ticket detail uses `textContent` for description; knowledge base sanitizes mammoth output (strips `<script>` and `on*` handlers); PDF/image preview URLs HTML-escaped.
- **Auth throttling** — `AuthViewSet.throttle_scope = "auth"` (10/min).
- **Tenant queryset scoping** — TenantViewSet filters by user's memberships (superusers see all).
- **File-upload MIME** — python-magic with content-type fallback (avatars, logos, attachments); 2MB avatar cap, 25MB general cap.
- **SSO fields** — `sso_client_id/authority_url/scopes` write-only in TenantSettings serializer.
- **Attachment cross-tenant** — `AttachmentUploadSerializer.validate()` ensures target object belongs to current tenant.
- **Stripe subscription tenant tracking** — `subscription_data.metadata.tenant_id` on checkout + webhook handler resolves tenant from subscription metadata.
- **Password validation** — full Django `validate_password()` (complexity + common-password list).

## Key Implementation Details

- **Ticket number per-tenant sequencing:** dedicated `TicketCounter` model (unscoped, SELECT FOR UPDATE) — replaces older max-number query approach.
- **Signal dedup flag:** set `instance._skip_signal_logging = True` before save; use `serializer.instance` in `perform_update` so the flag reaches the signal. 2-sec window.
- **Session cookie domain:** Dev `None` (per-origin); prod `.{BASE_DOMAIN}` (wildcard subdomains).
- **CSRF trusted origins:** Dev `http://localhost:8001` + `http://*.localhost:8001`; prod `https://*.{BASE_DOMAIN}`.
- **File upload path:** `tenants/{tenant_id}/attachments/YYYY/MM/{filename}` (attachments); `tenants/{tenant_id}/recordings/YYYY/MM/{uuid}.wav` (VoIP).
- **InboundEmail tenant resolution:** model extends `TimestampedModel` (NOT TenantScopedModel) — `tenant` FK nullable, set post-parse. Subject sanitized (strip `\r`/`\n`).
- **CannedResponse ownership:** only creator or Manager+ can edit/delete shared ones.
- **SavedView default race:** `set_default()` uses `transaction.atomic() + select_for_update()`.
- **SLA breach flag persistence:** `response_breached`/`resolution_breached` saved to DB before firing notifications (dedup across retries).
- **Ticket soft delete:** DELETE sets `is_deleted=True`, `deleted_at`, `deleted_by`; default queryset excludes soft-deleted; `?include_deleted=true` shows them; POST `restore/` reverses.
- **Ticket watchers:** duplicates return 409; `reason` (manual/mentioned/commented/cc'd); `is_muted` suppresses notifications; list annotates `watcher_count`.
- **Time tracking:** 1–1440 minute range, billable flag, optional started_at/ended_at, users delete only own entries. `time-summary/` aggregates.
- **Ticket templates:** `usage_count` via POST `use/`; active-only in list.
- **Circular ticket link prevention:** `_creates_circular_dependency` check — e.g. A→B→A blocked.
- **SLA filters:** `?sla_approaching=true` (≤30m), `?has_sla=true/false`, `?sla_response_breached`, `?sla_resolution_breached`.
- **Reminder status derivation:** computed property from completed_at/cancelled_at/scheduled_at.
- **Pipeline default race:** `is_default=True` setter atomically demotes prior default.
- **Macro application:** renders variables + executes actions atomically.
- **BusinessHours schedule default:** Mon–Fri 09:00–17:00, Sat–Sun off.
- **Reminder renamed from Recall** in crm migration 0003 (data-preserving).

## Common Pitfalls & Fixes Applied

1. `TenantSettings` dual-PK issue — removed `primary_key=True` from OneToOneField
2. Allauth config must be a set: `ACCOUNT_LOGIN_METHODS = {"email"}`
3. All apps need `migrations/__init__.py`
4. DRF upgraded 3.15.2 → 3.16.1 (Django 6.0 compatibility)
5. `base.html` needs `user.is_authenticated` check (AnonymousUser has no `.email`)
6. Role creation signal must include `hierarchy_level` (10/20/30)
7. Ticket stats JS reads `data.ticket_stats` (not `data.ticket_summary`)
8. Flower package added to requirements/base.txt
9. **Viewer role removed** — hierarchy is Admin/Manager/Agent only
10. `swagger_fake_view` check in `get_queryset()` to survive OpenAPI schema generation
11. Use `get_user_model()` (not direct import) in async consumers
12. Test fixtures: `UserFactory` uses `_after_postgeneration` with `skip_postgeneration_save = True`
13. Test base: `current_period_start/end` must be tz-aware datetimes
14. **`django-celery-beat` removed** — not Django 6 compatible; Celery built-in shelve scheduler (`celerybeat-schedule`) used instead
15. **VoIP queue note** — `kanzan_voip` is defined in `celery.py` routes but the default worker's `-Q` list does not include it; add `kanzan_voip` to `ecosystem.config.js` or start a dedicated VoIP worker before enabling VoIP tasks
16. **PM2 process count** — 5 processes (django, celery-worker, celery-beat, flower, **smtp**), not 4
17. **9 Beat tasks** including `fetch-inbound-emails` (60s), `calculate-lead-scores` (daily), `calculate-account-health-scores` (daily), `cleanup-stale-calls` (hourly), `kb-stale-alert`/`kb-gap-digest`
