# Kanzen Suite - Project Intelligence

## Project Overview

Multi-tenant CRM & Ticketing SaaS built with Django 6.0.2, DRF 3.16, Channels 4.2+, Celery 5.4+.
Bootstrap 5.3 frontend with vanilla JS. PM2 process management. Row-level multi-tenancy.

**Port:** 8001 | **Dev DB:** SQLite | **Prod DB:** PostgreSQL
**Redis:** db3 (cache/sessions), db4 (Celery broker), db5 (Channels layer)

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
/home/kavin/Kanzan/
├── apps/                          # 16 Django apps
│   ├── accounts/                  # Users, RBAC, permissions, invitations
│   ├── agents/                    # Agent availability/workload tracking
│   ├── analytics/                 # Reports, dashboard widgets, exports
│   ├── attachments/               # File uploads (polymorphic GenericFK)
│   ├── billing/                   # Stripe billing, plans, subscriptions
│   ├── comments/                  # Comments + ActivityLog (audit trail)
│   ├── contacts/                  # Contacts, companies, groups
│   ├── custom_fields/             # EAV custom fields per tenant
│   ├── inbound_email/             # Inbound email webhook processing → tickets
│   ├── kanban/                    # Visual boards, columns, card positions
│   ├── knowledge/                 # Knowledge base articles & categories
│   ├── messaging/                 # Real-time conversations (WebSocket)
│   ├── notes/                     # Quick sticky notes for agents
│   ├── notifications/             # Notifications + preferences (WebSocket)
│   ├── tenants/                   # Multi-tenant orgs, middleware, frontend views
│   └── tickets/                   # Core ticketing, SLA, escalation, email
├── main/                          # Django project root
│   ├── settings/{base,dev,prod}.py
│   ├── celery.py                  # Celery app + queue routing
│   ├── asgi.py                    # ASGI + WebSocket routing
│   ├── models.py                  # TimestampedModel, TenantScopedModel
│   ├── managers.py                # TenantAwareManager
│   ├── context.py                 # Thread-local tenant context
│   └── urls.py                    # Main URL router
├── templates/                     # base.html, includes/, pages/
├── static/css/                    # custom.css (14K+ lines)
├── static/js/                     # api.js, app.js, notes-panel.js, theme.js, etc.
├── tests/                         # pytest test suite (18 test files)
├── conftest.py                    # pytest fixtures & factories
├── pytest.ini                     # pytest configuration
├── requirements/{base,dev,prod}.txt
├── ecosystem.config.js            # PM2: 4 processes
├── docs/architecture.md           # 44KB architecture doc
└── .env                           # Environment variables
```

## Multi-Tenancy Architecture

### Three-Layer Isolation

1. **TenantMiddleware** (`apps/tenants/middleware.py`): Resolves tenant from subdomain (`{slug}.localhost`) or custom domain. Sets `request.tenant` and thread-local context. Exempt paths: `/admin/`, `/static/`, `/api/v1/accounts/auth/`, `/api/v1/billing/plans/`, `/api/v1/billing/webhook/`, `/api/docs/`, `/accounts/`, `/inbound/email/`.

2. **TenantAwareManager** (`main/managers.py`): Default `objects` manager auto-filters by `get_current_tenant()`. Use `Model.unscoped` for cross-tenant queries (admin, Celery tasks, management commands).

3. **TenantScopedModel** (`main/models.py`): Base class auto-assigns `tenant` on `save()`. Raises `ValueError` if no tenant context.

### Thread-Local Context (`main/context.py`)
```python
set_current_tenant(tenant)   # Set in middleware
get_current_tenant()         # Used by manager/model
clear_current_tenant()       # Cleanup in middleware finally block
```

## Models (55+ total)

### Base Models (Abstract)
- **TimestampedModel**: UUID PK + `created_at` + `updated_at`
- **TenantScopedModel**: Inherits Timestamped + `tenant` FK + auto-filtering

### Key Models by App

**tenants**: `Tenant` (name, slug, domain, is_active, logo), `TenantSettings` (1:1, auth_method, SSO config, branding, `inbound_email_address`, `business_days` JSON with validation (list of ints 0-6), `business_hours_start/end`, `accent_color`)

**accounts**: `User` (email-based, no username, UUID PK), `Permission` (29 total, codename pattern: `resource.action`), `Role` (tenant-scoped, hierarchy_level: Admin=10, Manager=20, Agent=30), `Profile` (tenant-specific: job_title, department, bio, notification_email, signature, timezone, language, DND settings, theme, sidebar_collapsed, density, date_format, time_format), `TenantMembership` (User+Tenant+Role bridge), `Invitation` (token-based, expiring)

**tickets**: `Ticket` (auto-number per tenant, status, priority, assignee, contact, queue, tags JSON, custom_data JSON), `TicketStatus` (customizable per tenant, is_closed, is_default), `TicketCategory` (admin-configurable categories per tenant), `Queue` (default_assignee, auto_assign), `SLAPolicy` (response/resolution minutes per priority), `EscalationRule` (trigger+action+target), `TicketActivity` (timeline events), `TicketAssignment` (immutable audit trail), `CannedResponse` (pre-written templates with shortcuts e.g. `/thanks`, usage counting, shared/personal), `SavedView` (saved filter configs, sort ordering, default/pinned, personal/shared scope)

**contacts**: `Contact` (email unique per tenant, company FK, groups M2M), `Company` (name unique per tenant), `ContactGroup` (M2M contacts)

**kanban**: `Board` (resource_type: TICKET/DEAL), `Column` (status mapping, WIP limit), `CardPosition` (polymorphic GenericFK, ordered, tenant-scoped)

**comments**: `Comment` (polymorphic GenericFK, threaded via parent), `Mention` (FK comment+user), `ActivityLog` (immutable audit trail, GenericFK, stores diffs+IP)

**messaging**: `Conversation` (type: direct/group/ticket, M2M participants via through), `ConversationParticipant` (last_read_at, is_muted), `Message` (threaded, mentions M2M)

**notifications**: `Notification` (9 types, recipient FK, is_read), `NotificationPreference` (per user+tenant+type, in_app+email channels)

**billing**: `Plan` (Free/Pro/Enterprise, limits + feature flags), `Subscription` (1:1 Tenant, Stripe sync), `Invoice` (Stripe sync), `UsageTracker` (1:1 Tenant, period counters)

**analytics**: `ReportDefinition` (7 types), `DashboardWidget` (5 types), `ExportJob` (CSV/XLSX/PDF, async via Celery)

**agents**: `AgentAvailability` (status: online/away/busy/offline, capacity tracking, `auto_away_outside_hours`)

**custom_fields**: `CustomFieldDefinition` (8 field types, 3 modules, role-based visibility), `CustomFieldValue` (EAV with typed columns: value_text, value_number, value_date, value_bool)

**knowledge**: `Category` (tenant-scoped, name, slug, description, ordering), `Article` (tenant-scoped, draft/published status, category FK, author, tags, view_count, pinned, file attachment support)

**notes**: `QuickNote` (tenant-scoped, personal sticky notes with color choices: yellow/blue/green/pink/purple/orange, pinning, ordering)

**inbound_email**: `InboundEmail` (extends TimestampedModel — NOT tenant-scoped at creation, tenant FK nullable; status: pending/processing/ticket_created/reply_added/rejected/failed; stores raw email data, sender, subject, body, attachment_metadata; links to ticket FK; Message-ID dedup)

## Polymorphic Models (GenericForeignKey)
- Comment, ActivityLog, Attachment, CustomFieldValue, CardPosition
- All use `content_type` FK + `object_id` UUID

## Role-Based Access Control

**Hierarchy:** Admin(10) <= Manager(20) <= Agent(30)  *(Viewer role removed)*

- `is_admin_or_manager`: `hierarchy_level <= 20` (context processor injects into all templates)
- `is_admin`: `hierarchy_level <= 10`
- `is_agent_or_above`: `hierarchy_level <= 30`
- Agent restriction (`level > 20`): sees only own tickets, linked contacts, filtered kanban cards
- `IsTenantMember` permission class (`accounts/permissions.py`): base permission ensuring authenticated users belong to the current tenant (prevents cross-tenant JWT access). Applied to most ViewSets.
- `IsTicketAccessible` permission in `accounts/permissions.py` blocks direct URL access
- `_role_required(20)` decorator on admin-only frontend views (settings, users, billing)
- Row-level filtering in: TicketViewSet, ContactViewSet, kanban BoardDetailSerializer, analytics services
- Expanded ACTION_MAP: contact group `add_contacts`/`remove_contacts` → "update"; kanban `detail_with_cards`/`populate` → "view", `move`/`reorder` → "update"

## Signals

### Tenant Signals (`apps/tenants/signals.py`)
- `Tenant.post_save` → `create_tenant_settings()` (TenantSettings via get_or_create)
- `Tenant.post_save` → `create_default_roles()` (3 roles: Admin/Manager/Agent)

### Account Signals (`apps/accounts/signals.py`)
- `TenantMembership.post_save` → `create_profile_on_membership()` (Profile per user per tenant)

### Ticket Signals (`apps/tickets/signals.py`)
- `Ticket.pre_save` → `handle_ticket_status_change()` (sets resolved_at/closed_at, stores old values for activity logging)
- `Ticket.post_save` → `fire_ticket_created_signal()` (emits custom `ticket_created` signal)
- `Ticket.post_save` → `fire_ticket_assigned_signal()` (emits custom `ticket_assigned` signal)
- `Ticket.post_save` → `log_ticket_activity()` (logs to ActivityLog with 2-second dedup, skips if `_skip_signal_logging` flag set)
- `Ticket.post_save` → `sync_kanban_card_on_status_change()` (moves card to column mapped to new status)

### Custom Fields Signals (`apps/custom_fields/signals.py`)
- `Ticket.post_save` → `sync_ticket_custom_fields()` (syncs CustomFieldValue from custom_data JSON)
- `Contact.post_save` → `sync_contact_custom_fields()` (same for contacts)

## Dual-Write Logging

**Two parallel log systems:**
1. **TicketActivity** (ticket timeline): Human-readable events for UI display. Endpoint: `/api/v1/tickets/tickets/{id}/timeline/`
2. **ActivityLog** (audit/compliance): Polymorphic, stores diffs+IP. Endpoint: `/api/v1/tickets/tickets/{id}/activity/`

**Dedup mechanism:** `_skip_signal_logging` flag on instance prevents signal from duplicating ViewSet logging. In `perform_update`, use `serializer.instance` (not `self.get_object()`) so flag reaches the signal. 2-second dedup window in signal handler.

**Service layer** (`apps/tickets/services.py`): `create_ticket_activity()`, `assign_ticket()`, `change_ticket_status()`, `change_ticket_priority()`, `log_ticket_comment()` — all write to BOTH logs atomically.

**Transaction safety:** Notification service defers WebSocket pushes and email task queuing to `transaction.on_commit()` to prevent orphaned tasks if the transaction rolls back.

## Inbound/Outbound Email System

### Inbound Email (`apps/inbound_email/`)
- **Webhook receiver** at `/inbound/email/` (not tenant-scoped, CSRF-exempt)
- Supports SendGrid, Mailgun, and generic webhook formats
- **Tenant resolution** via 3 patterns: plus-addressing (`support+{slug}@domain`), subdomain routing, custom `TenantSettings.inbound_email_address`
- **Ticket threading** via `[#N]` subject parsing and RFC 2822 In-Reply-To/Message-ID headers
- **Processing pipeline**: parse email → resolve tenant → find/create contact → create ticket or add reply → create comment
- **Async processing** via Celery task with transaction safety
- Message-ID dedup prevents duplicate processing

### Outbound Email (`apps/tickets/email_service.py`)
- `send_ticket_reply_email()` — sends agent replies to contacts
- `send_ticket_created_email()` — sends ticket confirmation
- Proper email threading: generates RFC-compliant Message-IDs, sets In-Reply-To and Reply-To headers
- HTML + plain text templates in `templates/tickets/email/`

## API Architecture

### Authentication
- **API:** JWT (SimpleJWT) — 15min access, 7-day refresh, rotate+blacklist
- **Frontend:** Session auth (Redis-backed, wildcard subdomain cookie)
- **SSO:** django-allauth (Google, Microsoft, OpenID Connect)

### API Prefix: `/api/v1/`

**Complete Endpoint Map:**
```
/api/v1/tenants/          → TenantViewSet, TenantSettingsViewSet (singleton)
/api/v1/accounts/         → AuthViewSet, UserViewSet, RoleViewSet, ProfileViewSet, InvitationViewSet, MembershipViewSet
/api/v1/tickets/          → TicketViewSet (+assign, change-status, change-priority, timeline, activity), TicketStatusViewSet, QueueViewSet, SLAPolicyViewSet, EscalationRuleViewSet
/api/v1/contacts/         → ContactViewSet, CompanyViewSet, ContactGroupViewSet
/api/v1/billing/          → PlanViewSet, SubscriptionViewSet (singleton +cancel/reactivate), InvoiceViewSet, UsageViewSet (singleton), checkout, webhook (CSRF-exempt)
/api/v1/kanban/           → BoardViewSet (+detail), ColumnViewSet, CardPositionViewSet (+move/reorder) [nested routing]
/api/v1/comments/         → CommentViewSet, ActivityLogViewSet (read-only)
/api/v1/messaging/        → ConversationViewSet, MessageViewSet [nested routing]
/api/v1/notifications/    → NotificationViewSet (+mark_read, unread_count), NotificationPreferenceViewSet
/api/v1/attachments/      → AttachmentViewSet (multipart upload)
/api/v1/analytics/        → DashboardView, ReportDefinitionViewSet, DashboardWidgetViewSet, ExportJobViewSet
/api/v1/agents/           → AgentAvailabilityViewSet
/api/v1/custom-fields/    → CustomFieldDefinitionViewSet, CustomFieldValueViewSet
/api/v1/knowledge/        → CategoryViewSet, ArticleViewSet (list/detail/create)
/api/v1/notes/            → QuickNoteViewSet
/api/v1/inbound-email/    → InboundEmailViewSet (read-only, all tenant members)
```

**Non-API Routes:**
```
/inbound/email/           → Inbound email webhook receiver (CSRF-exempt, not tenant-scoped)
```

**Docs:** `/api/docs/` (Swagger UI), `/api/schema/` (OpenAPI 3.0 JSON)

### REST Framework Config
- Pagination: PageNumberPagination, PAGE_SIZE=50
- Filtering: DjangoFilterBackend + SearchFilter + OrderingFilter
- Throttle rates: auth=10/min, api_default=200/min, api_heavy=30/min, webhook=60/min
- Renderers: JSON + BrowsableAPI

### Frontend Routes (`apps/tenants/frontend_urls.py`)
```
/                              → landing_page
/login/                        → login_page
/register/                     → register_page
/logout/                       → logout_page
/dashboard/                    → dashboard_page
/tickets/                      → ticket_list_page
/tickets/new/                  → ticket_create_page
/tickets/<number>/             → ticket_detail_page
/contacts/                     → contact_list_page
/contacts/create/              → contact_create_page
/contacts/<contact_id>/        → contact_detail_page
/calendar/                     → calendar_page
/kanban/                       → kanban_page
/messaging/                    → messaging_page
/analytics/                    → analytics_page
/users/                        → users_page (@_role_required(20))
/settings/                     → settings_page (@_role_required(20))
/billing/                      → billing_page (@_role_required(20))
/agents/                       → agents_page
/knowledge/                    → knowledge_list_page
/knowledge/<article_slug>/     → knowledge_article_page
/profile/                      → profile_page
/inbound-email/                → inbound_email_page (@_membership_required, all members)
```

## WebSocket Endpoints

1. **Chat:** `ws://host/ws/messaging/{conversation_id}/` → `ChatConsumer` (AsyncJsonWebsocketConsumer)
   - Actions: `send_message`, `typing`, `mark_read`
   - Group: `chat_{conversation_id}`
   - Validates participant membership, rejects anonymous
   - **Rate limiting:** MAX_MESSAGE_LENGTH=10KB, MAX_MESSAGES_PER_SECOND=5, TYPING_COOLDOWN=2s
   - **Tenant verification:** validates tenant from WebSocket scope to prevent cross-tenant access

2. **Notifications:** `ws://host/ws/notifications/` → `NotificationConsumer`
   - Actions: `mark_read`
   - Group: `notifications_{user_id}`
   - Service layer pushes via `notification_send` group event

## Celery Tasks

### Queue Routing (`main/celery.py`)
```
apps.billing.tasks.*                    → kanzan_webhooks
apps.notifications.tasks.send_email_*   → kanzan_email
apps.notifications.tasks.send_notification_email → kanzan_email
*                                       → kanzan_default
```

### Tasks
1. **`send_notification_email`** (notifications): Sends email for a Notification. max_retries=3, retry_delay=60s, acks_late. Template: `notifications/email/notification.html` with plain text fallback.
2. **`cleanup_old_notifications`** (notifications): Deletes read notifications older than N days (default 90). Batch deletion (1000 per batch) to prevent timeouts. Candidate for Celery Beat.
3. **`process_export_job`** (analytics): Generates CSV/XLSX export files. Supports tickets and contacts. max_retries=3. XLSX via openpyxl (optional, falls back to CSV).
4. **`process_inbound_email`** (inbound_email): Async processing of received inbound emails.
5. **Ticket email tasks** (tickets): Outbound email notifications for ticket replies and creation.

### PM2 Processes (`ecosystem.config.js`)
1. `kanzan-django`: Gunicorn + Uvicorn workers (2), port 8001, 2GB limit
2. `kanzan-celery-worker`: 4 concurrent, all 3 queues, prefork pool, 2GB limit
3. `kanzan-celery-beat`: Periodic task scheduler, 512MB limit
4. `kanzan-flower`: Monitoring dashboard, port 5556, 512MB limit

## Frontend Architecture

### JavaScript (`static/js/`)
- **api.js**: Centralized API client. CSRF from cookie, session credentials, JSON serialization, multipart upload support. Methods: `get()`, `post()`, `patch()`, `put()`, `delete()`, `upload()`.
- **app.js**: Global init. Auto-dismiss alerts (5s). Notification WebSocket connection. Toast system (`Toast.success/error/warning/info`). Cross-page toasts via sessionStorage.
- **notes-panel.js**: Quick notes UI panel (sticky notes CRUD, color selection, pinning).
- **theme.js**: Theme switching (light/dark/system).
- **agent-availability.js**: Agent status management UI.
- **command-palette.js**: Command palette / quick search.
- **custom-select.js**: Custom select dropdown component.
- **rich-editor.js**: Rich text editor for comments/articles.

### CSS (`static/css/custom.css` — 14K+ lines)
- Design system with CSS custom properties (blue primary `#2563EB`, dark theme)
- Components: sidebar (fixed, 272px, border-right, sticky with scroll), stat cards with left accent, soft badges, kanban cards with drag-and-drop, chat bubbles, timeline with dots, toast notifications, notes panel, knowledge base, calendar
- Responsive: sidebar collapses on mobile (<992px)
- Font: Inter, 0.875rem base

### Template Patterns
- Base: `templates/base.html` (sidebar + content area + toast container)
- Context processor injects: `tenant`, `membership`, `user_role`, `is_admin`, `is_admin_or_manager`, `is_agent_or_above`
- API calls use `Api` client with error handling and loading states
- Pagination: 25 items per page with prev/next
- Search: 400ms debounce

### Email Templates
- `templates/notifications/email/notification.html` + `.txt` — notification emails
- `templates/tickets/email/ticket_created.html` + `.txt` — new ticket confirmation
- `templates/tickets/email/reply_notification.html` + `.txt` — agent reply notification

## Middleware Stack (12 layers)
1. SecurityMiddleware
2. WhiteNoiseMiddleware (static files)
3. SessionMiddleware
4. CorsMiddleware
5. CommonMiddleware
6. CsrfViewMiddleware
7. AuthenticationMiddleware
8. AccountMiddleware (allauth)
9. **TenantMiddleware** (tenant resolution)
10. **SubscriptionMiddleware** (billing enforcement)
11. MessageMiddleware
12. XFrameOptionsMiddleware

## Third-Party Integrations

| Integration | Purpose | Config |
|-------------|---------|--------|
| Stripe | Payments, subscriptions | `STRIPE_SECRET_KEY/PUBLISHABLE_KEY/WEBHOOK_SECRET` |
| django-allauth | OAuth2 SSO (Google, Microsoft, OIDC) | `ACCOUNT_LOGIN_METHODS={"email"}` |
| Django Channels + channels-redis | WebSocket real-time | Redis db5, prefix `kanzan:channels` |
| django-redis | Cache + sessions | Redis db3, prefix `kanzan` |
| Celery + Redis broker | Background tasks | Redis db4, 3 queues |
| DRF-Spectacular | API docs (OpenAPI 3.0) | `/api/docs/` Swagger UI |
| SimpleJWT | JWT authentication | 15min access, 7-day refresh |
| django-filter | API filtering | DjangoFilterBackend + SearchFilter + OrderingFilter |
| WhiteNoise | Static file serving | CompressedManifestStaticFilesStorage (prod) |
| python-magic | MIME type validation | Server-side file type detection |
| mammoth | Word document handling | .docx → HTML conversion |
| Jazzmin | Admin theme | Customized sidebar, icons for 24+ models |
| Flower | Celery monitoring | Port 5556, basic auth |

## Billing Plans

| Plan | Users | Contacts | Tickets/mo | Storage | API | SSO | SLA |
|------|-------|----------|-----------|---------|-----|-----|-----|
| Free | 3 | 500 | 100 | 1GB | No | No | No |
| Pro | 25 | 10K | 5K | 25GB | Yes | No | Yes |
| Enterprise | Unlimited | Unlimited | Unlimited | Unlimited | Yes | Yes | Yes |

## Management Commands
```bash
python manage.py provision_tenant --name "Acme" --slug acme [--domain crm.acme.com]
python manage.py seed_plans                                    # Create Free/Pro/Enterprise
python manage.py setup_queues --tenant-slug demo               # Create 4 default queues
python manage.py setup_ticket_statuses --tenant-slug demo      # Create 5 default statuses
```

## Testing

### Test Infrastructure
- **Framework:** pytest + pytest-django
- **Config:** `pytest.ini` at project root (DJANGO_SETTINGS_MODULE configured)
- **Fixtures:** `conftest.py` with factories for Tenant, User, Role, Membership, TicketStatus, Queue, Ticket
- **Celery:** Eager mode in tests (tasks execute synchronously)

### Test Files (18 modules in `tests/`)
```
test_auth_rbac.py          — Authorization and RBAC
test_billing.py            — Billing system
test_contacts.py           — Contact management
test_custom_fields.py      — Custom fields
test_edge_cases.py         — Edge case scenarios
test_inbound_email.py      — Inbound email processing
test_kanban.py             — Kanban boards
test_multitenancy.py       — Multi-tenancy isolation
test_notifications.py      — Notification system
test_security.py           — Security and permissions
test_tickets.py            — Core ticketing
test_outbound_email.py     — Outbound email service
test_tenant_isolation.py   — Tenant data isolation
test_comment_visibility.py — Comment visibility rules
test_api_plan_enforcement.py — Plan limit enforcement
test_plan_limits.py        — Plan limit details
base.py                    — Shared test base classes
```

## Important Implementation Details

### Ticket Number Auto-Increment
Per-tenant sequential numbering via `Ticket.unscoped.filter(tenant_id=...).order_by("-number").first()`. Uses `unscoped` to avoid tenant filtering in save().

### Signal Dedup Flag
ViewSet sets `instance._skip_signal_logging = True` before save. Signal checks this flag to avoid duplicate logging. Must use `serializer.instance` (not `self.get_object()`) in `perform_update` so the flag persists.

### Session Cookie Domain
Dev: `SESSION_COOKIE_DOMAIN = None` (per-origin). Prod: `.{BASE_DOMAIN}` (wildcard subdomains).

### CSRF Trusted Origins
Dev: `http://localhost:8001`, `http://*.localhost:8001`. Prod: `https://*.{BASE_DOMAIN}`.

### File Upload Path
Tenant-isolated: `tenants/{tenant_id}/attachments/YYYY/MM/{filename}`. Max 25MB. MIME validated server-side (python-magic with content-type fallback).

### InboundEmail Tenant Resolution
`InboundEmail` extends `TimestampedModel` (not `TenantScopedModel`) because tenant is resolved during processing, not at creation. The `tenant` FK is nullable and set after parsing the recipient address. Subject lines are sanitized (strips `\r`/`\n`).

### Avatar Upload Validation
MIME type validation via python-magic (with content-type fallback), 2MB file size limit, image-only enforcement.

### Password Validation
Uses Django's full `validate_password()` (complexity rules) instead of simple length check.

### SSO Field Security
`TenantSettings` serializer marks SSO fields (`sso_client_id`, `sso_authority_url`, `sso_scopes`) as write-only to prevent leaking metadata to non-admin users.

### Attachment Cross-Tenant Protection
`AttachmentUploadSerializer.validate()` verifies the target object exists and belongs to the current tenant before allowing file attachment.

### Stripe Subscription Tenant Tracking
Checkout sessions include `subscription_data` metadata with `tenant_id`. Webhook handler resolves tenant from subscription metadata to prevent orphaned subscription records.

### CannedResponse Ownership
Only the creator or Manager+ can edit/delete shared canned responses. `created_by` is set automatically on creation.

### SavedView Default Race Protection
`set_default()` action uses `transaction.atomic()` + `select_for_update()` to prevent concurrent default changes.

### SLA Breach Tracking
Breach flags (`response_breached`, `resolution_breached`) are saved to DB before firing notifications, preventing duplicate breach notifications on retry. Ticket iteration uses `.iterator(chunk_size=200)`.

### First Response Race Condition Fix
`record_first_response()` uses atomic UPDATE with WHERE filter instead of save() to prevent race conditions with concurrent responses.

### Bulk Operations
`bulk_update_tickets()` handles failures independently (per-operation atomicity) so one failure doesn't rollback others.

## Performance Optimizations

### Analytics Closed Status Caching
Per-request cache (`_closed_status_cache`) for closed status IDs avoids repeated DB queries within `get_ticket_stats()`, `get_agent_performance()`, `get_due_today()`, `get_overdue_tickets()`. Cache cleared at end of `DashboardView.get()`.

### Kanban N+1 Fix
`BoardDetailSerializer.get_columns()` batch-fetches all content objects for GenericFK lookups (grouped by content_type, queried once per type), cached in `_content_object_cache`. Ticket objects pre-select related `status` and `assignee`.

### Kanban Board Population
`populate_board_from_tickets()` uses subquery-based `.exclude()` instead of loading all ticket IDs into memory.

### Comment Attachment Prefetching
Ticket detail endpoint batch-fetches all comment attachments, groups by comment ID, and sets `_prefetched_attachments` on each comment. `CommentSerializer.get_attachments()` checks for prefetched data before querying.

### Contact Group Bulk Add
Set-based logic for batch adding contacts: fetches existing IDs, filters new contacts, bulk adds in one operation (replaces loop with individual exists() checks).

### Company ViewSet Annotation
`CompanyViewSet.get_queryset()` annotates `contact_count` at the DB level. `ContactGroupSerializer` limits contacts to 50 and provides a separate `contact_count` field.

### Message Reply Count
`MessageViewSet` annotates `reply_count` via `Count("replies")` to avoid extra queries.

## Security Hardening

- **IsTenantMember permission**: Applied to AttachmentViewSet, BoardViewSet, ColumnViewSet, CardPositionViewSet, ContactGroupViewSet, ConversationViewSet, MessageViewSet, NotificationViewSet, NotificationPreferenceViewSet, QuickNoteViewSet. Prevents cross-tenant JWT access.
- **WebSocket rate limiting**: ChatConsumer enforces message length (10KB), send rate (5/sec), typing cooldown (2s), and tenant verification from scope.
- **XSS prevention**: Ticket detail uses `textContent` instead of `innerHTML` for description. Knowledge base sanitizes mammoth DOCX output (strips `<script>` tags and `on*` event handlers). PDF/image preview URLs are HTML-escaped.
- **Auth throttling**: `AuthViewSet` uses `throttle_scope = "auth"` for rate limiting.
- **Tenant queryset scoping**: `TenantViewSet` filters by user's actual memberships (superusers see all). Inbound email access simplified to all authenticated tenant members.
- **File upload MIME validation**: Avatar and tenant logo uploads use python-magic for true MIME detection with content-type fallback.

## Common Pitfalls & Fixes Applied
1. `TenantSettings` had dual primary key — removed `primary_key=True` from OneToOneField
2. Allauth config changed to `ACCOUNT_LOGIN_METHODS = {"email"}` (set, not list)
3. All apps needed `migrations/__init__.py` files
4. DRF upgraded 3.15.2 → 3.16.1 (format_suffix_patterns conflict with Django 6.0)
5. `base.html` needs `user.is_authenticated` check (AnonymousUser has no email)
6. Role creation signal was missing `hierarchy_level` in defaults — fixed to 10/20/30
7. Ticket stats JS read `data.ticket_summary` but API returns `data.ticket_stats`
8. Flower package not in requirements — `pip install flower`
9. Viewer role removed — hierarchy is now Admin(10)/Manager(20)/Agent(30) only
10. `swagger_fake_view` checks required in `get_queryset()` methods to prevent errors during OpenAPI schema generation
11. Use `get_user_model()` instead of direct User import in consumers/async code
12. Test fixtures: UserFactory uses `_after_postgeneration` with `skip_postgeneration_save = True` (factory_boy pattern)
13. Test base: `current_period_start/end` must be timezone-aware datetime objects, not strings
