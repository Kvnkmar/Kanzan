# Kanzen — Project Intelligence

## Project Overview

Multi-tenant CRM, Ticketing, Knowledge Base, and VoIP SaaS. **Django 6.0.2 + DRF 3.16 + Channels 4.2 + Celery 5.4** with Bootstrap 5.3.3 + vanilla JS frontend (SIP.js for softphone, TipTap for rich editor, DOMPurify for sanitization). Row-level multi-tenancy via subdomain routing and **contextvars-based** tenant binding (async-safe). PM2 process management.

**Port:** 8001 (ASGI via Gunicorn + Uvicorn worker) | **Dev DB:** SQLite | **Prod DB:** PostgreSQL
**Redis:** db3 (cache + cached_db sessions, prefix `kanzan`), db4 (Celery broker + django-db result backend), db5 (Channels layer, prefix `kanzan:channels`)
**SMTP in-process server:** 2525 (kanzan-smtp PM2 process)
**Flower:** 5556
**TIME_ZONE:** `Asia/Kuala_Lumpur` (Celery uses UTC; `USE_TZ=True`)

## Quick Reference

```
Superuser:      admin@epstein.local / Pl@nC-ICT_2024
Django Admin:   http://localhost:8001/admin/

Tenants:
  DPAP:         http://dpap.localhost:8001      (domain: asmra.shop)
  Meeting:      http://meeting.localhost:8001
  Debug:        http://debug-test.localhost:8001

Flower:         http://localhost:5556 (admin:changeme)
API Docs:       http://dpap.localhost:8001/api/docs/
```

## Project Structure

```
/home/kavin/Kanzen/
├── apps/                          # 20 Django apps (incl. apps.nav URL-only helper, no AppConfig)
│   ├── accounts/                  # Users, RBAC, permissions, invitations, profiles, middleware
│   ├── agents/                    # AgentAvailability + load-fairness email agent picker
│   ├── analytics/                 # Reports, dashboard widgets, exports, calendar events
│   ├── attachments/               # File uploads (polymorphic GenericFK)
│   ├── billing/                   # Stripe billing, plans, subscriptions, webhooks, decorators
│   ├── comments/                  # Comments + Mention + CommentRead + ActivityLog (audit)
│   ├── contacts/                  # Contacts, Companies, Accounts, Groups, ContactEvent (360°)
│   ├── crm/                       # Activity + Reminder, lead/account scoring, pipeline forecast
│   ├── custom_fields/             # EAV custom fields per tenant
│   ├── inbound_email/             # SMTP+IMAP ingestion → tickets; agent inbox workflow
│   ├── kanban/                    # Visual boards, columns, polymorphic CardPosition
│   ├── knowledge/                 # KB articles, categories, search, stale alerts, gap digest
│   ├── messaging/                 # Real-time conversations (WebSocket)
│   ├── nav/                       # URL-only helper (sidebar badge counts API — no AppConfig)
│   ├── newsfeed/                  # Internal announcements, reactions, read receipts
│   ├── notes/                     # Personal sticky notes (6 colors, pinning)
│   ├── notifications/             # In-app + email notifications + WebSocket
│   ├── tenants/                   # Tenant model, middleware, frontend views, frontend_urls
│   ├── tickets/                   # Core ticketing, SLA + business hours, CSAT, pipelines, macros, webhooks, deals
│   └── voip/                      # Asterisk ARI integration, SIP softphone, call logs, recordings, queues
├── main/                          # Django project root
│   ├── settings/{__init__,base,dev,prod}.py  # __init__ chooses dev/prod based on DJANGO_DEBUG
│   ├── celery.py                  # Celery app + 4-queue routing
│   ├── asgi.py                    # ProtocolTypeRouter: HTTP + WebSocket (5 consumer endpoints)
│   ├── context.py                 # contextvars-based tenant context (async-safe)
│   ├── models.py                  # TimestampedModel, TenantScopedModel
│   ├── managers.py                # TenantQuerySet, TenantAwareManager, SoftDeleteTenantManager
│   └── urls.py                    # /api/v1/ router + /api/docs/ + frontend URL include
├── templates/
│   ├── base.html                  # Layout, toast container, notes panel, softphone (conditional)
│   ├── includes/                  # navbar, sidebar, softphone, messages, kb_sidebar_widget
│   ├── pages/                     # 21 page folders (see Frontend Routes)
│   ├── auth/email/                # verify_email.{html,txt}
│   ├── knowledge/email/           # article_rejected.{html,txt}
│   ├── notifications/email/       # notification.{html,txt}
│   └── tickets/email/             # ticket_created, reply_notification, csat_survey (html+txt)
├── static/
│   ├── css/custom.css             # 20,394 lines (Crimson Black v9 design system)
│   ├── css/custom-v15.css         # Versioned copy referenced by base.html (uncommitted)
│   └── js/                        # 11 vanilla-JS modules (api, app, ticket-feed, voip-softphone, …)
├── tests/                         # 54 pytest modules at project root + tests/base.py
├── conftest.py                    # 16 factories + 20+ fixtures
├── pytest.ini                     # DJANGO_SETTINGS_MODULE=main.settings; pythonpath=.
├── requirements/{base,dev,prod}.txt
├── ecosystem.config.js            # PM2: 5 production processes
├── ecosystem.dev.config.js        # PM2 dev (4 processes; no SMTP, watch-mode reloads)
├── Makefile                       # 22 targets — dev/start/stop/restart/migrate/test/smoke/lint
├── docs/architecture.md           # Long-form architecture doc (953 lines, 12 sections + 5 appendices)
├── tmp/emails/                    # Dev email capture (filebased EmailBackend)
├── logs/                          # PM2 log files (one per process, error+out)
├── media/                         # User-uploaded files: tenants/{id}/… and inbound_emails/{id}/…
├── db.sqlite3                     # Dev database
├── celerybeat-schedule            # Celery Beat shelve file (built-in scheduler — django-celery-beat removed for Django 6 compat)
└── .env                           # Environment variables (not committed)
```

## Multi-Tenancy Architecture

### Three-Layer Isolation

1. **TenantMiddleware** (`apps/tenants/middleware.py`): Resolves tenant from subdomain (`{slug}.localhost`) or `TenantSettings.domain`. Sets `request.tenant` and binds context. Exempt paths include `/admin/`, `/static/`, `/media/`, `/api/v1/accounts/auth/`, `/api/v1/billing/plans/`, `/api/v1/billing/webhook/`, `/api/docs/`, `/accounts/`, `/login/`, `/register/`, `/logout/`, `/verify-email/*`, `/setup-company/`, `/workspaces/`.

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

## Models (~95 classes across 20 apps)

### Base Models (Abstract)
- **TimestampedModel**: UUID PK + `created_at` + `updated_at`; default ordering `["-created_at"]`
- **TenantScopedModel**: Inherits Timestamped + `tenant` FK (CASCADE, editable=False, db_index=True) + auto-filtering

### Tenants / Accounts

**tenants** (2 models):
- `Tenant` — name, slug (unique), domain (unique, custom domain, nullable), is_active, logo
- `TenantSettings` (1:1) — auth_method, SSO config, timezone, date_format, branding (`primary_color`, `accent_color`), `inbound_email_address`, business_hours_start/end, business_days (JSON), `auto_close_days` (5), `csat_delay_minutes` (60), `auto_transition_on_assign`, `auto_send_ticket_created_email`, **`auto_assign_inbound_email_tickets`** (migration 0008 — load-fairness agent picker)

**accounts** (6 models):
- `User` — email-based, UUID PK, includes `auth_version` for global logout
- `Permission` — global codenames (`{resource}.{action}`)
- `Role` — tenant-scoped, M2M permissions, `hierarchy_level` (10=Admin, 20=Manager, 30=Agent; **Viewer=40 exists in code but is removed from default seeding**)
- `Profile` — tenant-specific UI/agent prefs (theme, density, signature, DND, language, date/time format, sidebar_collapsed)
- `TenantMembership` — links User↔Tenant↔Role with is_active flag
- `Invitation` — token + expires_at
- `EmailVerificationToken` — pre-membership signup verification

### Tickets — heaviest app (22 model classes in `apps/tickets/models.py`)

**Core:**
- `Ticket` (~64 fields) — soft-delete (`is_deleted`/`deleted_at`/`deleted_by`); CSAT (`csat_rating`/`csat_comment`/`csat_submitted_at`); deal fields (`ticket_type`/`deal_value`/`expected_close_date`/`probability`/`pipeline_stage`/`account`/`won_at`/`lost_at`/`won_reason`/`lost_reason`); `merged_into`; `auto_close_task_id`; `pre_wait_status` (snapshot before pause); `tags` + `custom_data` JSONFields
- `TicketStatus` — `pauses_sla` flag, `is_closed`, `is_default`
- `TicketCategory`, `Queue` (with `default_assignee` + `auto_assign`), `TicketCounter` (per-tenant atomic sequencer using SELECT FOR UPDATE)
- `TicketActivity` — human-readable timeline with 26 event choices
- `TicketAssignment` — immutable audit log
- `TicketWatcher` — reasons (manual/mentioned/commented/cc), `is_muted`
- `TicketLink` — directed (duplicate_of/related_to/blocks/blocked_by) + circular-dependency guard
- `TimeEntry` — duration 1–1440 mins, billable, optional started_at/ended_at

**SLA + business hours:**
- `SLAPolicy`, `EscalationRule`, `BusinessHours` (per-day JSON schedule + IANA timezone), `PublicHoliday`, `SLAPause` (paused_at/resumed_at)

**Templates & productivity:**
- `CannedResponse` (variables + usage counter), `SavedView`, `TicketTemplate`, `Macro` (rendered body + JSON actions), `Webhook` (HMAC SHA-256, auto-disable at 10 failures)

**Deals:**
- `Pipeline` (is_default), `PipelineStage` (probability, is_won, is_lost)

### Contacts (5 models)

- `Contact` — email unique per tenant, company FK, account FK, groups M2M, `email_bouncing` flag (set by BounceLog), `last_activity_at`, `lead_score` (0-100, calculated nightly)
- `Company` — name unique per tenant, size enum, `contact_count` annotated
- `Account` — CRM account; `mrr`, `health_score` (0-100, calculated nightly)
- `ContactGroup` — M2M contacts
- `ContactEvent` — append-only 360° timeline aggregating ticket/activity/email events

### Inbound Email (3 models)

- `InboundEmail` — extends `TimestampedModel` (NOT TenantScopedModel — tenant nullable, resolved post-parse). Unified inbound + outbound (`direction` field). Status: `pending/processing/ticket_created/reply_added/sent/rejected/bounced/failed`. **Inbox workflow:** `inbox_status` (`pending/linked/actioned/ignored`), `linked_ticket`, `linked_at/by`, `actioned_at/by`, `action_taken` (OPEN/ASSIGN/CLOSE). Threading: `message_id`, `in_reply_to`, `references` (angle brackets stripped). Idempotency: `idempotency_key = "in:{tenant_id}:{message_id}"` (inbound) or `"out:{tenant_id}:{ticket_id}:{message_id}"` (outbound). Sanitized subject (strip `\r`/`\n`). `save()` enforces immutability of linked_at/by + actioned_at/by once set.
- `BounceLog` — hard-bounce records linked to InboundEmail + sender + reason + optional ticket
- `IMAPPollState` — host/user/mailbox + `uid_validity` + `last_uid` watermark (UIDNEXT-1, never backfill)

### VoIP (5 models)

- `VoIPSettings` — per-tenant singleton (asterisk_host/ari_port/wss_port, SSL toggle, encrypted ARI creds, STUN/TURN, default caller ID, `pjsip_context`, `recording_enabled`/`voicemail_enabled`/`is_active`)
- `Extension` — user→SIP endpoint (`sip_username` globally unique, encrypted `sip_password`, `caller_id_name/number`, `registered_at`)
- `CallLog` — direction (inbound/outbound/internal); status (ringing/answered/on_hold/completed/failed/missed/busy/no_answer/voicemail); timing fields; `asterisk_channel_id` indexed; metadata JSONField; FKs to caller/callee Extension, Contact, Ticket
- `CallRecording` — 1:1 CallLog; file → `tenants/{tenant_id}/recordings/{YYYY}/{MM}/{uuid}.{ext}`
- `CallQueue` — ACD strategy (ring_all/round_robin/least_recent/fewest_calls/random); M2M Extension members

### CRM (2 models — deal fields live on Ticket)

- `Activity` — type (call/email/meeting/task), subject, notes, due_at, completed_at, outcome; FKs ticket, contact, created_by, assigned_to
- `Reminder` (formerly `Recall` — renamed in migration 0003) — priority (low/medium/high/urgent); scheduled_at, completed_at, cancelled_at; **status is a derived property** (not stored); custom QuerySet: `.overdue()`/`.pending()`/`.for_user()`; methods: `mark_completed()`, `mark_cancelled()`, `reschedule(new_at, note)`

### Newsfeed (3 models)

- `NewsPost` — category (announcement/update/celebration/incident/general), is_pinned, is_published, is_urgent, emoji, expires_at; ordered pinned-first then newest
- `NewsPostReaction` — 6 emoji choices (thumbs_up/celebration/heart/rocket/eyes/hundred); one per user per post
- `NewsPostRead` — read tracking; row-existence = read flag; **NOT tenant-scoped**

### Knowledge Base

- `Category` — slug, ordering, icon
- `Article` — status (draft/pending_review/published/rejected/flagged); visibility (internal/public); review workflow fields (`reviewer`, `reviewed_at`, `submitted_at`, `rejection_reason`, `review_at`); `search_vector` (Postgres SearchVectorField, GIN-indexed); `view_count`, `is_pinned`, file/file_name (PDF/DOCX support via mammoth)
- Plus KBSearchGap / KBNotification / KBVote / KBFeedback used by gap-digest and stale-alert tasks

### Kanban (3 models)

- `Board` — resource_type (TICKET/DEAL), is_default
- `Column` — board FK, name, order, optional `status` FK to TicketStatus, `wip_limit`, color
- `CardPosition` — polymorphic GenericFK (content_type+object_id), order, tenant-scoped

### Comments / Activity / Messaging / Notifications

**comments** (4 models): `Comment` (polymorphic GenericFK, threaded via parent, `is_internal` flag), `Mention`, `CommentRead` (row-existence = read), `ActivityLog` (immutable polymorphic audit trail with diffs + IP)

**messaging** (3 models): `Conversation` (DIRECT/GROUP/TICKET), `ConversationParticipant` (last_read_at, is_muted), `Message` (threaded via parent, mentions M2M)

**notifications** (2 models): `Notification` (15 types incl. TICKET_ASSIGNED/UPDATED/COMMENT, MENTION, MESSAGE, SLA_BREACH, TICKET_OVERDUE, PAYMENT_FAILED, SUBSCRIPTION_CHANGE, INVITATION, AGENT_STATUS_CHANGE, TICKET_FOLLOWUP_OVERDUE, REMINDER_OVERDUE, KB_REVIEW_REQUESTED, KB_ARTICLE_REVIEWED), `NotificationPreference` (per user+tenant+type; in_app + email channels)

### Billing / Analytics / Agents / Custom Fields / Attachments / Notes

**billing**: `Plan` (Free/Pro/Enterprise tiers; **feature flags** including `has_voip`, `has_call_recording`, `max_calls_per_month`, `audit_retention_days`), `Subscription` (1:1 Tenant, Stripe sync, `in_grace_period` property), `Invoice`, `UsageTracker` (period counters incl. `calls_made` for VoIP)

**analytics**: `ReportDefinition`, `DashboardWidget`, `ExportJob` (Celery-backed CSV/XLSX/PDF; openpyxl optional with CSV fallback), `CalendarEvent`

**agents**: `AgentAvailability` (online/away/busy/offline, `max_concurrent_tickets`, `current_ticket_count`, `working_hours` JSON, `auto_away_outside_hours`)

**custom_fields**: `CustomFieldDefinition` (8 types × 3 modules: TICKET/CONTACT/COMPANY; role visibility), `CustomFieldValue` (EAV: value_text/number/date/bool)

**attachments**: `Attachment` (polymorphic GenericFK, tenant-isolated path `tenants/{tenant_id}/attachments/YYYY/MM/{filename}`)

**notes**: `QuickNote` (6 colors: yellow/blue/green/pink/purple/orange; pinning, position)

## Polymorphic (GenericForeignKey) Models
`Comment`, `ActivityLog`, `Attachment`, `CustomFieldValue`, `CardPosition` — all use `content_type` FK + `object_id` UUID.

## Role-Based Access Control

**Hierarchy:** Admin(10) ≤ Manager(20) ≤ Agent(30) *(Viewer=40 exists in code; removed from default role seeding)*

- `is_admin`: `hierarchy_level ≤ 10`
- `is_admin_or_manager`: `≤ 20` (context processor injects into every template)
- `is_agent_or_above`: `≤ 30`
- Agent restriction (`level > 20`): sees only own/assigned tickets, linked contacts, filtered kanban cards, own reminders/activities
- **Permission classes** (`accounts/permissions.py`): `HasTenantPermission` (codename-based with ACTION_MAP + fallback hierarchy defaults), `IsTicketAccessible` (object-level), `IsTenantMember`, `IsTenantAdmin`, `IsTenantAdminOrManager`
- `_role_required(20)` decorator gates admin/manager frontend pages (settings, users, billing, agents, audit_log)
- Row-level filtering in: `TicketViewSet`, `ContactViewSet`, `ReminderViewSet`, `ActivityViewSet`, `BoardDetailSerializer`, analytics services

## Signals

### Tenant (`apps/tenants/signals.py`)
- `Tenant.post_save` → `create_tenant_settings`, `create_default_roles` (Admin/Manager/Agent with hierarchy_level + permissions from ROLE_DEFINITIONS)

### Accounts (`apps/accounts/signals.py`)
- `TenantMembership.post_save` → `create_profile_on_membership`

### Tickets (`apps/tickets/signals.py`) — 8+ handlers
- `Ticket.pre_save` → `handle_ticket_status_change` (sets resolved_at/closed_at, stashes old values, checks resolution breach)
- `Ticket.post_save` → `fire_ticket_created_signal` + `fire_ticket_assigned_signal` (custom signals → webhooks)
- `Ticket.post_save` → `log_ticket_activity` (writes ActivityLog; 2-second dedup window; skips if `_skip_signal_logging` flag set)
- `Ticket.post_save` → `sync_kanban_card_on_status_change`, `sync_kanban_card_on_pipeline_stage_change`, `create_kanban_card_on_ticket_save`
- `Ticket.post_save` → `handle_sla_pause_on_status_change` / `_resume_sla_pause` (creates/closes `SLAPause` when entering/leaving a `pauses_sla` status; shifts deadlines forward by business-adjusted pause duration)
- `Ticket.post_save` → `check_kb_article_coverage` (flags ticket if category has <3 published articles)
- `SLAPolicy.post_save` → `propagate_sla_policy_change` (recalculates deadlines for affected open tickets; async if >50)

### Custom Fields (`apps/custom_fields/signals.py`)
- `Ticket.post_save`/`Contact.post_save` → sync `CustomFieldValue` from JSON `custom_data`

### Knowledge (`apps/knowledge/signals.py`)
- Article review status transitions → audit log + email notifications

### VoIP (`apps/voip/signals.py`)
- `CallLog.post_save` on terminal status (COMPLETED/MISSED/FAILED/BUSY/NO_ANSWER/VOICEMAIL) → writes `TicketActivity` + `comments.ActivityLog` + queues `process_call_recording` when enabled. Uses `_timeline_logged` dedup flag.

## Dual-Write Logging

**Two parallel log systems:**
1. **TicketActivity** (human-readable timeline) — endpoint: `/api/v1/tickets/tickets/{id}/timeline/`
2. **ActivityLog** (polymorphic audit trail with diffs+IP) — endpoint: `/api/v1/tickets/tickets/{id}/activity/`

**Dedup:** ViewSet sets `instance._skip_signal_logging = True` before save; signal checks flag. Use `serializer.instance` (not `self.get_object()`) in `perform_update` so the flag persists. 2-sec window in signal.

**Service layer** (`apps/tickets/services.py`): `create_ticket_activity`, `assign_ticket`, `transition_ticket_status`, `change_ticket_priority`, `log_ticket_comment`, `close_ticket`, `escalate_ticket`, `merge_tickets`, `split_ticket`, `bulk_update_tickets`, `apply_macro`, `record_first_response`, `transition_pipeline_stage`, `initialize_sla`, `broadcast_ticket_event`, `validate_status_transition`, `resume_from_wait`, `_extend_sla_on_escalation` — all write to BOTH logs atomically. Broadcast defers WebSocket push to `transaction.on_commit()`.

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
- **IMAP poller** (`imap_poller.py`) — shared Gmail-style mailbox; `poll_once()` fetches by UID > watermark (NOT UNSEEN — Gmail marks seen instantly). Driven by `fetch_inbound_emails_task` (Celery Beat, every 60s). Disabled when `IMAP_HOST` is blank. **Safety guarantee:** never backfills — aborts the poll if UIDVALIDITY/UIDNEXT can't be parsed (recent fix: regex extracts bare integers from bracketed `OK` response or untagged_responses dict, with `select_resp` fallback).
- **Tenant resolution** — 3 patterns via `resolve_tenant_from_address`: plus-addressing (`support+{slug}@...`), subdomain routing, custom `TenantSettings.inbound_email_address`. Fallback to `IMAP_DEFAULT_TENANT_SLUG` if configured.
- **Filters** (`filters.py`) run BEFORE tenant resolution (cheap): loop detection (sender == `DEFAULT_FROM_EMAIL`), noreply senders, RFC 3834 Auto-Submitted / Precedence: bulk/junk/list, subject patterns. `classify_email()` returns `bounce` / `auto_reply` / `loop` / `legitimate`. Bounces write `BounceLog` and flip `Contact.email_bouncing=True`.
- **Threading** (`threading.py`) — `find_existing_ticket` uses 3-tier priority: In-Reply-To → References (reversed, most-recent first) → subject `[#N]` regex. All queries tenant-scoped. Outbound: `build_thread_headers(tenant, ticket, new_message_id)` reads last 10 related InboundEmails for Message-ID chain.
- **Processing pipeline** (Celery `process_inbound_email_task`, max_retries=3, default_retry_delay=30s, acks_late): `select_for_update` lock → filter classifier → tenant resolution → idempotency claim → find/create contact → find existing ticket OR create new + init SLA + auto-tag "email" + **maybe auto-assign** (`_maybe_auto_assign` calls `auto_assign_email_ticket()` when `TenantSettings.auto_assign_inbound_email_tickets=True`) → attach files (stored under `inbound_emails/{pk}/`) → queue confirmation email via `transaction.on_commit()`.
- **Agent inbox workflow** (`inbox_services.py`): `link_email_to_ticket`, `action_email` (OPEN/ASSIGN/CLOSE), `ignore_email`. Mutations atomic; `linked_at/by`, `actioned_at/by` immutable once set (enforced in `InboundEmail.save()`).
- **Utils** (`utils.py`): `normalize_message_id`, `normalize_references`, `extract_header` (handles RFC 2822 folding), `parse_sender`, `strip_quoted_reply` (Gmail/Outlook/Apple Mail quoted blocks).

### Outbound (`apps/tickets/email_service.py`)
- `send_ticket_email()` is the single entry point.
- RFC-compliant Message-IDs; sets In-Reply-To, References, Reply-To.
- Persists an OUTBOUND `InboundEmail` record so future replies can be threaded via Message-ID lookup.
- Dev default backend: `django.core.mail.backends.filebased.EmailBackend` → writes to `tmp/emails/`. Prod: SMTP via `EMAIL_HOST/PORT/USER/PASSWORD/USE_TLS`.
- Legacy wrappers: `send_ticket_reply_email`, `send_ticket_created_email`, `send_csat_survey_email` (all dispatched async via `send_ticket_*` Celery tasks).

## Auto-Assign (Inbound Email → Agent)

`apps/agents/services.py::pick_email_agent(tenant)`:
1. Active tenant member with **`hierarchy_level == 30`** (pure Agent — excludes Admin/Manager)
2. Must NOT be OFFLINE; agents with no `AgentAvailability` row are eligible
3. Pick the one with **fewest open tickets** (load balancing)
4. Tie-break by **least-recently-assigned** (`MAX(TicketAssignment.created_at)`, NULLS FIRST for cold-start fairness)

`auto_assign_email_ticket(ticket)` — atomically saves assignment, creates `TicketAssignment` audit row with note `"Auto-assigned from inbound email (load + fairness)"`, best-effort nudges `AgentAvailability.current_ticket_count` (F-expression). Failures are logged but swallowed (auto-assign is convenience, not correctness).

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
- **API:** JWT (SimpleJWT) — 15min access, 7-day refresh, rotate + blacklist, HS256
- **Frontend:** Session auth (Redis-backed cached_db, host-only cookie in dev)
- **SSO:** django-allauth (Google, Microsoft, OpenID Connect) — `ACCOUNT_LOGIN_METHODS = {"email"}`
- **Global logout:** `User.auth_version` bumped invalidates all prior sessions; `SessionVersionMiddleware` enforces

### `/api/v1/` Endpoint Map (from `main/urls.py`, 22 router includes)
```
/tenants/            TenantViewSet, TenantSettingsViewSet (singleton)
/accounts/           AuthViewSet (throttle_scope="auth"), UserViewSet, RoleViewSet, ProfileViewSet, InvitationViewSet, MembershipViewSet
/tickets/            TicketViewSet + 30+ custom actions, TicketStatusViewSet, QueueViewSet, TicketCategoryViewSet, SLAPolicyViewSet, EscalationRuleViewSet, CannedResponseViewSet, MacroViewSet, SavedViewViewSet, BusinessHoursViewSet, PublicHolidayViewSet, TicketTemplateViewSet, WebhookViewSet, CSATSubmitView (public, no auth)
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
/nav/                BadgeCountView (GET /badge-counts/) — capped at 99 per category
/newsfeed/           NewsPostViewSet (+react/mark-read/mark-all-read/unread-count)
/voip/               VoIPSettingsViewSet, ExtensionViewSet, CallLogViewSet (+active_calls, call_stats), InitiateCallView, CallHoldView, CallTransferView, CallHangupView, SIPCredentialsView, CallRecordingDownloadView, CallQueueViewSet
```

**Non-HTTP inbound channel:** the `kanzan-smtp` PM2 process accepts mail on `SMTP_SERVER_HOST:SMTP_SERVER_PORT` (default `0.0.0.0:2525`) and feeds the same `InboundEmail` + Celery pipeline.

**Docs:** `/api/docs/` (Swagger UI), `/api/schema/` (OpenAPI 3.0 JSON).

### TicketViewSet — Custom @action Map (30+)
- **Mutations:** `assign`, `close`, `change_status`, `change_stage`, `escalate`, `restore`, `merge`, `split`
- **Timeline & comments:** `comments`, `activity`, `timeline`, `mark_all_read`
- **Email:** `emails`, `send_email`, `send_creation_email`, `link_email`, `unlinked_emails`
- **Linking:** `links`, `delete_link`
- **Macros & bulk:** `apply_macro`, `bulk_action`
- **Watchers:** `watchers`, `watch`, `remove_watcher`
- **Time:** `time_entries`, `time_summary`
- **Search:** `lookup` (number-only, ignores soft-delete), `search`, `teammates`, `team_progress`

### REST Framework Config
- Pagination: PageNumberPagination, PAGE_SIZE=50
- Filtering: DjangoFilterBackend + SearchFilter + OrderingFilter
- Throttle rates: `auth=10/min`, `api_default=200/min`, `api_heavy=30/min`, `webhook=60/min` (ScopedRateThrottle)
- Renderers: JSON + BrowsableAPI

### Frontend Routes (`apps/tenants/frontend_urls.py`) — 28 paths
```
/                             landing_page
/login/                       login_page
/register/                    register_page
/logout/                      logout_page
/auth/handoff/                auth_handoff
/verify-email/                verify_email_page
/verify-email-sent/           verify_email_sent_page
/setup-company/               setup_company_page
/workspaces/                  workspaces_page
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
/reminders/                   reminders_page
/audit-log/                   audit_log_page        @_role_required(20)
/calls/                       calls_page            (VoIP call history)
```

## WebSocket Endpoints (5 total, wired in `main/asgi.py`)

Stack: `AllowedHostsOriginValidator` → `AuthMiddlewareStack` → `WebSocketTenantMiddleware` → `URLRouter(messaging_ws + notification_ws + ticket_ws + voip_ws)`

1. **Chat:** `ws/messaging/{conversation_id}/` → `ChatConsumer`. Actions: `send_message`, `typing`, `mark_read`. Group: `chat_{conversation_id}`. Rate limits: MAX_MESSAGE_LENGTH=10KB, MAX_MESSAGES_PER_SECOND=5, TYPING_COOLDOWN=2s. Validates participant membership + tenant.
2. **Notifications:** `ws/notifications/` → `NotificationConsumer`. Group: `notifications_{user_id}`. Inbound action: `{"action": "mark_read", "notification_id": "<uuid>"}`.
3. **Ticket Presence:** `ws/tickets/{ticket_id}/presence/` → `TicketPresenceConsumer`. Events: `agent_joined`, `agent_left`, `presence_list`. Group: `ticket_{ticket_id}_presence`. Heartbeat support.
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
| `knowledge_base.alert_stale_articles` | crontab daily 08:00 |
| `knowledge_base.send_gap_digest` | crontab Monday 09:00 |
| `apps.voip.tasks.cleanup_stale_calls` | 3600s (hourly) |
| `apps.inbound_email.tasks.fetch_inbound_emails_task` | 60s |

> Celery Beat uses the **built-in shelve scheduler** (`celerybeat-schedule` file at repo root). `django-celery-beat` was removed — incompatible with Django 6.0.

### Celery Task Inventory
- **notifications**: `send_notification_email` (retries=3, default_retry_delay=60s, acks_late), `cleanup_old_notifications` (batch 1000)
- **analytics**: `process_export_job` (retries=3, default_retry_delay=60s; CSV/XLSX; openpyxl optional with CSV fallback)
- **inbound_email**: `fetch_inbound_emails_task` (calls IMAP `poll_once()`), `process_inbound_email_task` (retries=3, default_retry_delay=30s, acks_late)
- **tickets**: `check_sla_breaches` (iterator chunk_size=200, dedup escalation rules, persists breach flag before notify), `check_overdue_tickets` (daily dedup per ticket), `send_ticket_reply_email_task`, `send_ticket_created_email_task`, `send_ticket_email_task`, `auto_close_ticket`, `send_csat_survey_email`, `deliver_webhook_task`, `check_sla_breach_warnings`, `propagate_sla_policy_change_task`
- **voip**: `process_call_recording` (retries=3, default_retry_delay=60s), `cleanup_stale_calls`, `sync_call_state` (retries=3, default_retry_delay=30s)
- **crm**: `check_overdue_reminders` (every 15 min — note: not currently in Beat schedule; runs implicitly when called), `calculate_lead_scores`, `calculate_account_health_scores`

## PM2 Processes (`ecosystem.config.js`) — 5 total

| Name | Script | Purpose | Memory |
|------|--------|---------|--------|
| `kanzan-django` | `gunicorn main.asgi:application -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8001 --timeout 120 --graceful-timeout 30` | HTTP + WebSocket (ASGI) | 2GB |
| `kanzan-celery-worker` | `celery -A main worker -Q kanzan_default,kanzan_email,kanzan_webhooks -c 4 --pool prefork --max-tasks-per-child=200` | Background jobs | 2GB |
| `kanzan-celery-beat` | `celery -A main beat` | Periodic scheduler | 512MB |
| `kanzan-flower` | `celery -A main flower --port=5556 --url_prefix=flower --basic_auth=$KANZAN_FLOWER_AUTH` | Monitoring dashboard | 512MB |
| `kanzan-smtp` | `python manage.py run_smtp_server` | In-process SMTP (port 2525) | 512MB |

> **Note:** the worker's `-Q` list is `kanzan_default,kanzan_email,kanzan_webhooks`. The `kanzan_voip` queue is defined in `main/celery.py` routes, but VoIP tasks only run when `kanzan_voip` is added to `-Q` or a dedicated VoIP worker is started. `run_ari_listener` is **not in PM2** by default — start separately if VoIP is live.

**Dev (`ecosystem.dev.config.js`):** 4 processes (no SMTP), Django uses `runserver` for auto-reload, worker watches `apps/*/tasks.py`, `apps/*/services.py`, `main/celery.py` (delay 2s). Lower memory caps.

## Frontend Architecture

### JavaScript (`static/js/`, 11 modules — vanilla, no React/Vue)
- **api.js** — Central API client (CSRF from cookie, session credentials, JSON + multipart). Methods: `get/post/patch/put/delete/upload`.
- **app.js** — Global init: auto-dismiss alerts (5s), notification WebSocket, `Toast.{success,error,warning,info}`, cross-page toasts via sessionStorage, `Kanzan.formatDate()`/`formatDateTime()`/`timeAgo()` localization per user prefs, sidebar badge polling, density preference (comfortable/compact via `data-density`).
- **ticket-feed.js** — WebSocket `ws/tickets/feed/`. Auto-connects via `data-ticket-feed` attribute or URL match. Toasts + "new tickets" banner + row pulse. Reconnect with exponential backoff (max 10 attempts, 30s cap).
- **voip-softphone.js** — SIP.js 0.21.2 (CDN: `cdn.jsdelivr.net/npm/sip.js@0.21.2/lib/platform/web/sip.js`) + `CallEventConsumer`. Dial pad, DTMF, mute/hold/transfer/hangup, incoming-call modal.
- **notes-panel.js** — Quick notes CRUD (6 colors, pinning, localStorage).
- **theme.js** — light/dark/system (default: dark). Persists to localStorage `kanzan_theme`. Listens to `prefers-color-scheme: dark` matchMedia changes (recently enhanced).
- **agent-availability.js** — Status toggle + persistence.
- **command-palette.js** — Cmd+K modal: 12 static pages, 2 quick actions, dynamic search.
- **custom-select.js** — `KanzenSelect` global with portal rendering + searchable when >8 options.
- **rich-editor.js** — TipTap wrapper for comments/articles.
- **keyboard-shortcuts.js** — Global hotkeys: j/k navigate, Enter open, Esc deselect; a/s/x row actions; Ctrl+K palette; c new ticket; ? help; g d/t/c/b go-to. `.keyboard-selected` class. Disabled inside inputs.

### CSS (`static/css/custom.css` — 20,394 lines; **`custom-v15.css`** is uncommitted versioned copy referenced by `base.html`)
- **Design system: "Crimson Black v9"** — deep red (`#C1121F`) primary, brighter red (`#E11D2D`) accent, light grays for surfaces, high-contrast text. Sidebar 252px white with 3px red ::before bar (active only). Strict dark-mode-first (default theme = dark).
- CSS custom properties under `:root`: `--crm-primary*` 9-step scale, `--crm-bg-*`, `--crm-text-*`, `--crm-sidebar-*`, `--crm-status-{success,warning,danger,info}`, `--crm-priority-{urgent,high,medium,low}`, `--crm-chat-*`, `--crm-kanban-*`, `--crm-radius-{sm,}` (8px/10px), `--crm-font-family` (Inter)
- Components: stat cards with left accent, soft badges, kanban drag-and-drop, chat bubbles, timeline dots, toast notifications, notes panel, knowledge base, calendar, **softphone widget (~L18984)**, **audit log tabs/stats (~L18054)**, command palette (~L13612), quick notes (~L17040)
- **Disabled tenant primary_color override** in `base.html` (lines 29-30 commented out) — design system enforces strict red/black/white palette
- Font: Inter (Google Fonts), 0.875rem fluid base; mobile collapses sidebar <992px

### Templates
- `templates/base.html` — layout + toast container + quick-notes panel + **softphone include (conditional on `voip_enabled`)** + DOMPurify v3.2.4 CDN + SIP.js 0.21.2 CDN (conditional). Mobile detection script adds `is-mobile` / `is-mobile-sm` body classes. Default theme: dark.
- `templates/includes/` — `navbar.html`, `sidebar.html`, `softphone.html`, `messages.html`, `kb_sidebar_widget.html`
- `templates/pages/` — 21 folders: agents, analytics, audit_log, auth (4 pages), billing, calendar.html, contacts, dashboard.html, emails, inbound_email, kanban, knowledge, landing.html, login.html, messaging, profile.html, register.html, reminders, settings, tickets, users, voip
- Email templates: `auth/email/verify_email.{html,txt}`, `tickets/email/{ticket_created,reply_notification,csat_survey}.{html,txt}`, `notifications/email/notification.{html,txt}`, `knowledge/email/article_rejected.{html,txt}`

### Context Processor (`apps/tenants/context_processors.py`)
Injects into every template: `tenant`, `membership`, `user_role`, `is_admin`, `is_admin_or_manager`, `is_agent_or_above`, **`voip_enabled`** (controls softphone inclusion), `BASE_URL`.

## Middleware Stack (13 layers)
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
12. MessageMiddleware
13. XFrameOptionsMiddleware

## Third-Party Integrations

| Integration | Version | Purpose | Config |
|-------------|---------|---------|--------|
| Django | 6.0.2 | Framework | — |
| DRF | ≥3.16,<4 | REST API | — |
| Channels | ≥4.2,<5 | WebSocket real-time | Redis db5 |
| channels-redis | ≥4.2,<5 | Channel layer | prefix `kanzan:channels` |
| Celery | ≥5.4,<6 | Background tasks | Redis db4 broker, django-db results, 4 queues |
| django-celery-results | ≥2.5,<3 | Celery result store | django-db |
| django-redis | ≥5.4,<6 | Cache + sessions | Redis db3, prefix `kanzan` |
| redis | ≥5.2,<6 | Redis client | — |
| psycopg | ≥3.2,<4 | PostgreSQL driver (binary) | DATABASE_URL |
| Stripe | ≥11,<12 | Payments, subscriptions | STRIPE_* envs |
| django-allauth | ≥65,<66 | OAuth2 SSO (Google, Microsoft, OIDC) | `ACCOUNT_LOGIN_METHODS={"email"}` |
| SimpleJWT | ≥5.4,<6 | JWT auth | 15m access, 7d refresh, rotate+blacklist, HS256 |
| PyJWT | ≥2.9,<3 | JWT primitive | — |
| DRF-Spectacular | ≥0.28,<0.29 | OpenAPI 3.0 docs | `/api/docs/` |
| django-filter | ≥24.3,<25 | API filtering | DjangoFilterBackend |
| django-cors-headers | ≥4.6,<5 | CORS | — |
| django-environ | ≥0.12,<1 | Env config | reads `.env` |
| WhiteNoise | ≥6.8,<7 | Static serving | CompressedManifestStaticFilesStorage in prod |
| python-magic | ≥0.4,<0.5 | MIME detection | Avatar/logo/attachment uploads |
| Pillow | ≥11,<12 | Image processing | Avatars, logos |
| mammoth | ≥1.12,<2 | `.docx` → HTML | Knowledge base imports |
| openpyxl | ≥3.1,<4 | Excel export | analytics ExportJob |
| aiosmtpd | ≥1.4,<2 | In-process SMTP server | Inbound email |
| httpx | ≥0.27,<1 | Async HTTP | Asterisk ARI client |
| websockets | ≥12,<14 | WebSocket client | ARI Stasis events |
| SIP.js | 0.21.2 (CDN) | Browser SIP/WebRTC | VoIP softphone |
| Bootstrap | 5.3.3 (CDN) | CSS framework | — |
| Tabler Icons | 3.31.0 (CDN) | Icon webfont | — |
| DOMPurify | 3.2.4 (CDN) | XSS sanitization | Ticket detail, KB articles |
| Jazzmin | ≥3.0,<4 | Admin theme | Custom sidebar + 24+ model icons |
| daphne | ≥4.2,<5 | ASGI server | Listed in INSTALLED_APPS |
| gunicorn | ≥25,<26 | WSGI/ASGI server | Production |
| uvicorn[standard] | ≥0.40,<1 | ASGI worker | Production |
| Flower | ≥2.0,<3 | Celery monitoring | Port 5556 |

**Dev tools:** pytest ≥8.3, pytest-django ≥4.9, pytest-asyncio ≥0.24, pytest-cov ≥6, factory-boy ≥3.3, faker ≥33, ruff ≥0.8, django-debug-toolbar ≥4.4, django-extensions ≥3.2, ipython ≥8.31

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
python manage.py seed_plans                                    # Free/Pro/Enterprise
python manage.py setup_queues --tenant-slug demo               # 4 default queues
python manage.py setup_ticket_statuses --tenant-slug demo      # 5 default statuses
python manage.py backfill_sla_audit                            # baseline SLA audit for in-flight tickets

# Long-running daemons
python manage.py run_smtp_server                               # kanzan-smtp PM2 process
python manage.py run_ari_listener                              # VoIP Stasis event loop (NOT in PM2 — start separately if VoIP is live)
```

## Environment Variables

### In `.env.example` (16 keys)
`DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DATABASE_URL`, `REDIS_URL`, `BASE_DOMAIN`, `BASE_SCHEME`, `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`, `JWT_SECRET_KEY`, `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `KANZAN_FLOWER_AUTH`

### Read by `base.py` but NOT in `.env.example`
- **Base:** `BASE_PORT`
- **IMAP:** `IMAP_HOST`, `IMAP_PORT`, `IMAP_USER`, `IMAP_PASSWORD`, `IMAP_MAILBOX`, `IMAP_USE_SSL`, `IMAP_DEFAULT_TENANT_SLUG`
- **SMTP server:** `SMTP_SERVER_HOST`, `SMTP_SERVER_PORT`, `SMTP_SERVER_HOSTNAME`, `SMTP_SERVER_REQUIRE_AUTH`, `SMTP_SERVER_AUTH_USERS` (JSON dict), `SMTP_SERVER_TLS_CERT_FILE`, `SMTP_SERVER_TLS_KEY_FILE`
- **Inbound webhook (unused today):** `INBOUND_EMAIL_WEBHOOK_SECRET`
- **Email:** `EMAIL_BACKEND`, `DEFAULT_FROM_EMAIL`, `EMAIL_TIMEOUT`, `EMAIL_USE_SSL`

`main/settings/__init__.py` loads `base.py` then conditionally loads `dev.py` (when `DJANGO_DEBUG=True`) or `prod.py`. `pytest.ini` sets `DJANGO_SETTINGS_MODULE=main.settings`.

## Testing

### Infrastructure
- **Framework:** pytest + pytest-django (54 root test modules + 3 app-level)
- **Config:** `pytest.ini` — `DJANGO_SETTINGS_MODULE=main.settings`, `pythonpath=.`
- **Fixtures (`conftest.py`):** 16 factories (Tenant, User, Role with admin/manager/agent/viewer traits, Membership, TicketStatus, Queue, Ticket, Company, Contact, ContactGroup, Notification, Plan, Subscription, CustomFieldDefinition, Reminder, InboundEmail) + 20+ fixtures including `free_plan` (autouse), `clear_tenant_context` (autouse), `admin_client/manager_client/agent_client/viewer_client/anon_client`
- **Legacy base** (`tests/base.py`): `TenantTestCase` providing `tenant_a`/`tenant_b` + admin_a/agent_a/admin_b
- **Celery:** Eager mode (autouse fixture `celery_eager`)

### Test Suite Coverage (~57 modules)
- **Tickets/SLA:** test_tickets, test_ticket_creation/lifecycle/assignment/improvements/linking/split/presence, test_first_response, test_closure, test_phase{3,4}_*, test_pre_wait_status, test_auto_transition_toggle, test_recalls, test_macros, test_csat, test_badges, test_sla{,_pause,_business_hours,_escalation_extension,_audit_logging}
- **Email:** test_email{,_inbound,_outbound,_auto_assign}, test_inbound_email, test_outbound_email, test_imap_poller_safety, test_bounce_handling
- **Security & tenancy:** test_auth, test_auth_rbac, test_access_control, test_critical_security, test_security, test_multitenancy, test_tenant_isolation, test_comment_visibility, test_contact_context
- **Other:** test_kanban, test_billing, test_billing_limits, test_api_plan_enforcement, test_plan_limits, test_comments, test_notifications, test_contacts, test_custom_fields, test_kb, test_knowledge_base, test_audit, test_edge_cases, test_crm
- **App-level:** `apps/tickets/tests/{test_creation,test_escalation}.py`, `apps/knowledge/tests/test_kb_gap_fill.py`

### Recent Test Additions (uncommitted)
- **`tests/test_email_auto_assign.py`** — covers `pick_email_agent` selection policy (load + fairness), atomic auto-assign + audit, end-to-end pipeline respects tenant toggle.
- **`tests/test_imap_poller_safety.py`** — regression suite for "never backfill" guarantee. Verifies poll aborts when UIDVALIDITY/UIDNEXT unreadable; happy path anchors watermark at UIDNEXT-1; ambient digits in human-readable text don't fool the parser.

## Performance Optimizations

- **Analytics closed-status cache** — per-request `_closed_status_cache` in `DashboardView` avoids repeated DB lookups across `get_ticket_stats`/`get_agent_performance`/`get_due_today`/`get_overdue_tickets`.
- **Kanban N+1 fix** — `BoardDetailSerializer.get_columns` batch-fetches GenericFK content objects grouped by content_type; Tickets pre-select `status` and `assignee`.
- **Kanban populate** — `populate_board_from_tickets` uses subquery `.exclude()` instead of loading all ticket IDs.
- **Comment attachment prefetching** — ticket detail batch-fetches and sets `_prefetched_attachments`.
- **Contact group bulk add** — set-based batch (one query) instead of per-contact `exists()` check.
- **Company `contact_count` annotation** — at DB level; `ContactGroupSerializer` caps contacts at 50.
- **Message reply count** — annotated via `Count("replies")`.
- **SLA breach iteration** — `iterator(chunk_size=200)` to bound memory.
- **First-response race** — atomic UPDATE + WHERE filter (no `save()`).
- **Bulk ops** — `bulk_update_tickets` handles failures independently per operation.
- **Lead/health scoring** — pre-fetches signal sets (ContactEvent, Activity, Ticket, CSAT), iterates contacts via `.iterator(chunk_size={500,200})`, bulk-updates by score bucket.
- **Reminder overdue task** — Reminder.unscoped + iterator(chunk_size=200), 1-per-day dedup via Notification filter on data__reminder_id.

## Security Hardening

- `IsTenantMember` applied to AttachmentViewSet, BoardViewSet, ColumnViewSet, CardPositionViewSet, ContactGroupViewSet, ConversationViewSet, MessageViewSet, NotificationViewSet, NotificationPreferenceViewSet, QuickNoteViewSet — blocks cross-tenant JWT access.
- ChatConsumer rate limits (10KB msg, 5/sec, 2s typing cooldown) and tenant-from-scope verification. TicketPresenceConsumer, TicketListConsumer, CallEventConsumer all verify tenant membership.
- **Webhook `secret`** write-only in serializer responses; HMAC SHA-256 signing; auto-disable at 10 consecutive failures.
- **XSS prevention** — ticket detail uses `textContent` for description; knowledge base sanitizes mammoth output (strips `<script>` and `on*` handlers); PDF/image preview URLs HTML-escaped; DOMPurify available globally for client-side sanitization.
- **Auth throttling** — `AuthViewSet.throttle_scope = "auth"` (10/min).
- **Tenant queryset scoping** — `TenantViewSet` filters by user's memberships (superusers see all).
- **File-upload MIME** — python-magic with content-type fallback (avatars, logos, attachments); 2MB avatar cap; 25MB general cap (`FILE_UPLOAD_MAX_MEMORY_SIZE`/`DATA_UPLOAD_MAX_MEMORY_SIZE`).
- **SSO fields** — `sso_client_id/authority_url/scopes/secret` write-only in TenantSettings serializer.
- **Attachment cross-tenant** — `AttachmentUploadSerializer.validate()` ensures target object belongs to current tenant.
- **Stripe subscription tenant tracking** — `subscription_data.metadata.tenant_id` on checkout + webhook handler resolves tenant from subscription metadata.
- **Password validation** — full Django `validate_password()` (complexity + common-password list).
- **Global logout via auth_version** — `SessionVersionMiddleware` invalidates sessions when user.auth_version is bumped.
- **InboundEmail immutability** — `linked_at/by` and `actioned_at/by` raise `ValidationError` if changed once set.
- **IMAP "never backfill"** — poll aborts cleanly rather than ingesting historical mail when UIDVALIDITY/UIDNEXT can't be parsed.

## Key Implementation Details

- **Ticket number per-tenant sequencing:** dedicated `TicketCounter` model (unscoped, SELECT FOR UPDATE) — replaces older max-number query approach.
- **Signal dedup flag:** set `instance._skip_signal_logging = True` before save; use `serializer.instance` in `perform_update` so the flag reaches the signal. 2-sec window.
- **Session cookie domain:** Dev host-only (per-origin); prod also host-only due to Chrome's strict `.localhost` policy. Cross-tenant handoffs use signed tokens.
- **CSRF trusted origins:** Dev `http://localhost:8001` + `http://*.localhost:8001`; prod `https://*.{BASE_DOMAIN}`.
- **File upload paths:** `tenants/{tenant_id}/attachments/YYYY/MM/{filename}` (attachments); `tenants/{tenant_id}/recordings/YYYY/MM/{uuid}.{ext}` (VoIP); `inbound_emails/{pk}/{filename}` (inbound).
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
- **Macro application:** renders `{{ticket.*}}`, `{{contact.*}}`, `{{agent.*}}`, `{{ticket.queue}}` variables + executes actions atomically.
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
9. **Viewer role removed** from default seeding — hierarchy is Admin/Manager/Agent only
10. `swagger_fake_view` check in `get_queryset()` to survive OpenAPI schema generation
11. Use `get_user_model()` (not direct import) in async consumers
12. Test fixtures: `UserFactory` uses `_after_postgeneration` with `skip_postgeneration_save = True`
13. Test base: `current_period_start/end` must be tz-aware datetimes
14. **`django-celery-beat` removed** — not Django 6 compatible; Celery built-in shelve scheduler (`celerybeat-schedule`) used instead
15. **VoIP queue note** — `kanzan_voip` is defined in `celery.py` routes but the default worker's `-Q` list does not include it; add `kanzan_voip` to `ecosystem.config.js` or start a dedicated VoIP worker before enabling VoIP tasks
16. **PM2 process count** — 5 processes (django, celery-worker, celery-beat, flower, **smtp**), not 4
17. **9 Beat tasks** including `fetch-inbound-emails` (60s), `calculate-lead-scores` (daily), `calculate-account-health-scores` (daily), `cleanup-stale-calls` (hourly), `kb-stale-alert`/`kb-gap-digest`
18. **CSS versioning** — `static/css/custom-v15.css` is the live file referenced by `base.html` (uncommitted; identical to `custom.css` content); the older `?v=redtheme-1` query param was replaced.
19. **IMAP "never backfill" safety** — UIDVALIDITY/UIDNEXT must be parseable to bare integers; the poller aborts (returns 0) on first run rather than match `1:*`. See `tests/test_imap_poller_safety.py`.
20. **Tenant primary_color override disabled** in `templates/base.html` (lines ~29-30) — the design system enforces a strict red/black/white palette; per-tenant theming now happens via logo/accent only.

## Documentation
- `docs/architecture.md` — 953-line authoritative architecture doc (12 sections + 5 appendices: API map, WS endpoints, Celery queues, env vars, default credentials)
- `README.md` — minimal stub (1 line); rely on this CLAUDE.md and `docs/architecture.md` for context
