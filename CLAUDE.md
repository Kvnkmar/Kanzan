# Kanzen Suite - Project Intelligence

## Project Overview

Multi-tenant CRM & Ticketing SaaS built with Django 6.0.2, DRF 3.16, Channels 4.2+, Celery 5.4+.
Bootstrap 5.3 frontend with vanilla JS. PM2 process management. Row-level multi-tenancy.

**Port:** 8001 | **Dev DB:** SQLite | **Prod DB:** PostgreSQL
**Redis:** db3 (cache/sessions), db4 (Celery broker), db5 (Channels layer)

## Quick Reference

```
Dev URL:        http://demo.localhost:8001
Superuser:      admin@kanzan.local / Pl@nC-ICT_2024
Demo tenant:    slug=demo
Flower:         http://localhost:5556 (admin:changeme)
API Docs:       http://demo.localhost:8001/api/docs/
Django Admin:   http://localhost:8001/admin/
```

## Project Structure

```
/home/kavin/Kanzen Suite/
├── apps/                          # 13 Django apps
│   ├── accounts/                  # Users, RBAC, permissions, invitations
│   ├── agents/                    # Agent availability/workload tracking
│   ├── analytics/                 # Reports, dashboard widgets, exports
│   ├── attachments/               # File uploads (polymorphic GenericFK)
│   ├── billing/                   # Stripe billing, plans, subscriptions
│   ├── comments/                  # Comments + ActivityLog (audit trail)
│   ├── contacts/                  # Contacts, companies, groups
│   ├── custom_fields/             # EAV custom fields per tenant
│   ├── kanban/                    # Visual boards, columns, card positions
│   ├── messaging/                 # Real-time conversations (WebSocket)
│   ├── notifications/             # Notifications + preferences (WebSocket)
│   ├── tenants/                   # Multi-tenant orgs, middleware, frontend views
│   └── tickets/                   # Core ticketing, SLA, escalation
├── main/                          # Django project root
│   ├── settings/{base,dev,prod}.py
│   ├── celery.py                  # Celery app + queue routing
│   ├── asgi.py                    # ASGI + WebSocket routing
│   ├── models.py                  # TimestampedModel, TenantScopedModel
│   ├── managers.py                # TenantAwareManager
│   ├── context.py                 # Thread-local tenant context
│   └── urls.py                    # Main URL router
├── templates/                     # base.html, includes/, pages/
├── static/{css,js}/               # custom.css (1296 lines), api.js, app.js
├── requirements/{base,dev,prod}.txt
├── ecosystem.config.js            # PM2: 4 processes
├── docs/architecture.md           # 44KB architecture doc
└── .env                           # Environment variables
```

## Multi-Tenancy Architecture

### Three-Layer Isolation

1. **TenantMiddleware** (`apps/tenants/middleware.py`): Resolves tenant from subdomain (`{slug}.localhost`) or custom domain. Sets `request.tenant` and thread-local context. Exempt paths: `/admin/`, `/static/`, `/api/v1/accounts/auth/`, `/api/v1/billing/plans/`, `/api/v1/billing/webhook/`, `/api/docs/`, `/accounts/`.

2. **TenantAwareManager** (`main/managers.py`): Default `objects` manager auto-filters by `get_current_tenant()`. Use `Model.unscoped` for cross-tenant queries (admin, Celery tasks, management commands).

3. **TenantScopedModel** (`main/models.py`): Base class auto-assigns `tenant` on `save()`. Raises `ValueError` if no tenant context.

### Thread-Local Context (`main/context.py`)
```python
set_current_tenant(tenant)   # Set in middleware
get_current_tenant()         # Used by manager/model
clear_current_tenant()       # Cleanup in middleware finally block
```

## Models (48 total)

### Base Models (Abstract)
- **TimestampedModel**: UUID PK + `created_at` + `updated_at`
- **TenantScopedModel**: Inherits Timestamped + `tenant` FK + auto-filtering

### Key Models by App

**tenants**: `Tenant` (name, slug, domain, is_active, logo), `TenantSettings` (1:1, auth_method, SSO config, branding)

**accounts**: `User` (email-based, no username, UUID PK), `Permission` (29 total, codename pattern: `resource.action`), `Role` (tenant-scoped, hierarchy_level: Admin=10, Manager=20, Agent=30, Viewer=40), `Profile` (tenant-specific user metadata), `TenantMembership` (User+Tenant+Role bridge), `Invitation` (token-based, expiring)

**tickets**: `Ticket` (auto-number per tenant, status, priority, assignee, contact, queue, tags JSON, custom_data JSON), `TicketStatus` (customizable per tenant, is_closed, is_default), `Queue` (default_assignee, auto_assign), `SLAPolicy` (response/resolution minutes per priority), `EscalationRule` (trigger+action+target), `TicketActivity` (timeline events), `TicketAssignment` (immutable audit trail)

**contacts**: `Contact` (email unique per tenant, company FK, groups M2M), `Company` (name unique per tenant), `ContactGroup` (M2M contacts)

**kanban**: `Board` (resource_type: TICKET/DEAL), `Column` (status mapping, WIP limit), `CardPosition` (polymorphic GenericFK, ordered)

**comments**: `Comment` (polymorphic GenericFK, threaded via parent), `Mention` (FK comment+user), `ActivityLog` (immutable audit trail, GenericFK, stores diffs+IP)

**messaging**: `Conversation` (type: direct/group/ticket, M2M participants via through), `ConversationParticipant` (last_read_at, is_muted), `Message` (threaded, mentions M2M)

**notifications**: `Notification` (9 types, recipient FK, is_read), `NotificationPreference` (per user+tenant+type, in_app+email channels)

**billing**: `Plan` (Free/Pro/Enterprise, limits + feature flags), `Subscription` (1:1 Tenant, Stripe sync), `Invoice` (Stripe sync), `UsageTracker` (1:1 Tenant, period counters)

**analytics**: `ReportDefinition` (7 types), `DashboardWidget` (5 types), `ExportJob` (CSV/XLSX/PDF, async via Celery)

**agents**: `AgentAvailability` (status: online/away/busy/offline, capacity tracking)

**custom_fields**: `CustomFieldDefinition` (8 field types, 3 modules, role-based visibility), `CustomFieldValue` (EAV with typed columns: value_text, value_number, value_date, value_bool)

## Polymorphic Models (GenericForeignKey)
- Comment, ActivityLog, Attachment, CustomFieldValue, CardPosition
- All use `content_type` FK + `object_id` UUID

## Role-Based Access Control

**Hierarchy:** Admin(10) <= Manager(20) <= Agent(30) < Viewer(40)

- `is_admin_or_manager`: `hierarchy_level <= 20` (context processor injects into all templates)
- Agent+Viewer restriction (`level > 20`): sees only own tickets, linked contacts, filtered kanban cards
- `IsTicketAccessible` permission in `accounts/permissions.py` blocks direct URL access
- `_role_required(20)` decorator on admin-only frontend views (settings, users, billing)
- Row-level filtering in: TicketViewSet, ContactViewSet, kanban BoardDetailSerializer, analytics services

## Signals

### Tenant Signals (`apps/tenants/signals.py`)
- `Tenant.post_save` → `create_tenant_settings()` (TenantSettings via get_or_create)
- `Tenant.post_save` → `create_default_roles()` (4 roles: Admin/Manager/Agent/Viewer)

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
```

**Docs:** `/api/docs/` (Swagger UI), `/api/schema/` (OpenAPI 3.0 JSON)

### REST Framework Config
- Pagination: PageNumberPagination, PAGE_SIZE=50
- Filtering: DjangoFilterBackend + SearchFilter + OrderingFilter
- Throttle rates: auth=10/min, api_default=200/min, api_heavy=30/min, webhook=60/min
- Renderers: JSON + BrowsableAPI

### Frontend Routes (`apps/tenants/frontend_urls.py`)
```
/                    → landing_page
/login/              → login_page
/register/           → register_page
/logout/             → logout_page
/dashboard/          → dashboard_page
/tickets/            → ticket_list_page
/tickets/new/        → ticket_create_page
/tickets/<number>/   → ticket_detail_page
/contacts/           → contact_list_page
/kanban/             → kanban_page
/messaging/          → messaging_page
/users/              → users_page (@_role_required(20))
/settings/           → settings_page (@_role_required(20))
/billing/            → billing_page (@_role_required(20))
```

## WebSocket Endpoints

1. **Chat:** `ws://host/ws/messaging/{conversation_id}/` → `ChatConsumer` (AsyncJsonWebsocketConsumer)
   - Actions: `send_message`, `typing`, `mark_read`
   - Group: `chat_{conversation_id}`
   - Validates participant membership, rejects anonymous

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
2. **`cleanup_old_notifications`** (notifications): Deletes read notifications older than N days (default 90). Candidate for Celery Beat.
3. **`process_export_job`** (analytics): Generates CSV/XLSX export files. Supports tickets and contacts. max_retries=3. XLSX via openpyxl (optional, falls back to CSV).

### PM2 Processes (`ecosystem.config.js`)
1. `kanzan-django`: Gunicorn + Uvicorn workers (2), port 8001, 2GB limit
2. `kanzan-celery-worker`: 4 concurrent, all 3 queues, prefork pool, 2GB limit
3. `kanzan-celery-beat`: Periodic task scheduler, 512MB limit
4. `kanzan-flower`: Monitoring dashboard, port 5556, 512MB limit

## Frontend Architecture

### JavaScript (`static/js/`)
- **api.js**: Centralized API client. CSRF from cookie, session credentials, JSON serialization, multipart upload support. Methods: `get()`, `post()`, `patch()`, `put()`, `delete()`, `upload()`.
- **app.js**: Global init. Auto-dismiss alerts (5s). Notification WebSocket connection. Toast system (`Toast.success/error/warning/info`). Cross-page toasts via sessionStorage.

### CSS (`static/css/custom.css` — 1296 lines)
- Design system with CSS custom properties (crimson primary `#DC2626`, dark theme)
- Components: sidebar (fixed, 250px), stat cards with left accent, soft badges, kanban cards with drag-and-drop, chat bubbles, timeline with dots, toast notifications
- Responsive: sidebar collapses on mobile (<992px)
- Font: Inter, 0.875rem base

### Template Patterns
- Base: `templates/base.html` (sidebar + content area + toast container)
- Context processor injects: `tenant`, `membership`, `user_role`, `is_admin_or_manager`
- API calls use `Api` client with error handling and loading states
- Pagination: 25 items per page with prev/next
- Search: 400ms debounce

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
| Jazzmin | Admin theme | Customized sidebar, icons for 24 models |
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
Tenant-isolated: `tenants/{tenant_id}/attachments/YYYY/MM/{filename}`. Max 25MB. MIME validated server-side.

## Test Coverage
**No tests exist yet.** Architecture doc lists "Full test suite (unit, integration, E2E)" as Phase 2.

## Common Pitfalls & Fixes Applied
1. `TenantSettings` had dual primary key — removed `primary_key=True` from OneToOneField
2. Allauth config changed to `ACCOUNT_LOGIN_METHODS = {"email"}` (set, not list)
3. All 13 apps needed `migrations/__init__.py` files
4. DRF upgraded 3.15.2 → 3.16.1 (format_suffix_patterns conflict with Django 6.0)
5. `base.html` needs `user.is_authenticated` check (AnonymousUser has no email)
6. Role creation signal was missing `hierarchy_level` in defaults — fixed to 10/20/30/40
7. Ticket stats JS read `data.ticket_summary` but API returns `data.ticket_stats`
8. Flower package not in requirements — `pip install flower`
