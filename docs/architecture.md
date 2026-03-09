# Kanzen Suite & Ticketing SaaS Platform — Architecture Document

> **Version:** 1.0
> **Date:** 2026-02-06
> **Stack:** Python 3.12 · Django 6.0.2 · DRF 3.16 · Bootstrap 5.3 · Redis · Celery · Channels · SQLite (dev) / PostgreSQL (prod)

---

## Table of Contents

1. [System Architecture](#1-system-architecture)
2. [Multi-Tenant Strategy](#2-multi-tenant-strategy)
3. [App Responsibility Map](#3-app-responsibility-map)
4. [Database Schema](#4-database-schema)
5. [Tenant Routing Design](#5-tenant-routing-design)
6. [Auth & SSO Flow](#6-auth--sso-flow)
7. [Custom Fields Architecture](#7-custom-fields-architecture)
8. [Stripe Billing Flow](#8-stripe-billing-flow)
9. [Security Threat Model](#9-security-threat-model)
10. [Tool & Library Recommendations](#10-tool--library-recommendations)
11. [Development Roadmap](#11-development-roadmap)
12. [Common SaaS Failure Avoidance](#12-common-saas-failure-avoidance)

---

## 1. System Architecture

### 1.1 High-Level Component Diagram

```
                          ┌─────────────────┐
                          │   Web Browser    │
                          │  (Bootstrap 5.3) │
                          └────────┬─────────┘
                                   │ HTTP / WebSocket
                          ┌────────▼─────────┐
                          │      Nginx       │
                          │  (reverse proxy) │
                          │  port 80 / 443   │
                          └────────┬─────────┘
                                   │ proxy_pass :8001
                          ┌────────▼─────────┐
                          │ Gunicorn+Uvicorn │
                          │  (ASGI server)   │
                          │  port 8001       │
                          │  2 workers       │
                          └────────┬─────────┘
                    ┌──────────────┼──────────────┐
                    │              │              │
           ┌────────▼───┐  ┌──────▼──────┐ ┌─────▼──────┐
           │   Django   │  │  Channels   │ │  DRF API   │
           │  Templates │  │  WebSocket  │ │  (JSON)    │
           │  (HTML)    │  │  Consumers  │ │ /api/v1/*  │
           └────────┬───┘  └──────┬──────┘ └─────┬──────┘
                    │              │              │
                    └──────────────┼──────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
     ┌────────▼──────┐   ┌────────▼──────┐   ┌────────▼──────┐
     │   Database    │   │    Redis      │   │   Celery      │
     │  SQLite (dev) │   │   6379        │   │  Workers (4)  │
     │  PostgreSQL   │   │  db3: cache   │   │  Beat (1)     │
     │  (prod)       │   │  db4: broker  │   │  Flower :5556 │
     └───────────────┘   │  db5: channels│   └───────────────┘
                         └───────────────┘
```

### 1.2 Process Architecture (PM2)

| Process | Command | Purpose |
|---------|---------|---------|
| `kanzan-django` | `gunicorn main.asgi:application -k uvicorn.workers.UvicornWorker` | ASGI server (HTTP + WebSocket) |
| `kanzan-celery-worker` | `celery -A main worker -Q kanzan_default,kanzan_email,kanzan_webhooks -c 4` | Background task processing |
| `kanzan-celery-beat` | `celery -A main beat` | Periodic task scheduler |
| `kanzan-flower` | `celery -A main flower --port=5556` | Celery monitoring dashboard |

### 1.3 Request Lifecycle

```
Request → Nginx → Gunicorn/Uvicorn
  → SecurityMiddleware
  → WhiteNoiseMiddleware (static files)
  → SessionMiddleware
  → CorsMiddleware
  → CommonMiddleware
  → CsrfViewMiddleware
  → AuthenticationMiddleware
  → AccountMiddleware (allauth)
  → TenantMiddleware (resolve tenant from host)
  → SubscriptionMiddleware (enforce billing status)
  → MessageMiddleware
  → XFrameOptionsMiddleware
  → View / API Endpoint
  → Response
```

### 1.4 Port & Resource Isolation

| Resource | Kanzen Suite | Tempest (co-hosted) |
|----------|---------|---------------------|
| HTTP port | 8001 | 8000 |
| Redis cache | db3 | separate |
| Redis broker | db4 | separate |
| Redis channels | db5 | separate |
| Celery queues | `kanzan_*` | `tempest_*` |
| Flower port | 5556 | 5555 |
| OS user | kavin | ubuntu |

---

## 2. Multi-Tenant Strategy

### 2.1 Architecture Choice: Shared Schema, Row-Level Isolation

The platform uses a **shared-schema, shared-database** multi-tenancy model where all tenants' data lives in the same database tables, isolated by a `tenant_id` foreign key on every row.

**Why this approach:**
- Simplest operational model (single database to back up, migrate, monitor)
- Efficient resource utilisation (no per-tenant DB overhead)
- Easy cross-tenant admin queries when needed
- Scales to hundreds of tenants before needing sharding

### 2.2 Isolation Mechanism: Three-Layer Defense

```
Layer 1: TenantMiddleware
  ├── Extracts tenant from request host (subdomain or custom domain)
  ├── Sets request.tenant
  └── Injects tenant into thread-local context

Layer 2: TenantAwareManager (automatic queryset filtering)
  ├── Every model.objects.all() auto-filters by current tenant
  ├── Uses thread-local get_current_tenant()
  └── Falls back to unfiltered for admin/management commands

Layer 3: TenantScopedModel.save() (write protection)
  ├── Auto-assigns tenant from thread-local on save
  └── Raises ValueError if no tenant context exists
```

### 2.3 Base Models

```python
# main/models.py

class TimestampedModel(Model):
    """Global models: UUID PK + created_at/updated_at"""
    id = UUIDField(primary_key=True, default=uuid4)
    created_at = DateTimeField(auto_now_add=True)
    updated_at = DateTimeField(auto_now=True)

class TenantScopedModel(TimestampedModel):
    """Tenant-isolated models: + tenant FK + auto-scoping"""
    tenant = ForeignKey("tenants.Tenant", CASCADE, editable=False)
    objects = TenantAwareManager()   # auto-filtered
    unscoped = Manager()             # cross-tenant (admin only)
```

### 2.4 Thread-Local Tenant Context

```python
# main/context.py
_thread_locals = threading.local()

set_current_tenant(tenant)   # Called by TenantMiddleware
get_current_tenant()         # Used by TenantAwareManager
clear_current_tenant()       # Called in middleware finally block
```

### 2.5 Model Classification

| Category | Models | Scoping |
|----------|--------|---------|
| Global (shared) | `User`, `Permission`, `Plan` | No tenant FK |
| Per-tenant | `Ticket`, `Contact`, `Role`, `Conversation`, etc. | `TenantScopedModel` |
| Bridge (user↔tenant) | `TenantMembership` | Explicit `tenant` FK (not auto-scoped) |
| Billing | `Subscription`, `Invoice`, `UsageTracker` | Explicit `tenant` FK (global visibility) |

---

## 3. App Responsibility Map

### 3.1 Overview

```
┌─────────────────────────────────────────────────────────┐
│                    main (core layer)                     │
│  TimestampedModel · TenantScopedModel · TenantAwareManager │
│  Thread-local context · Settings · ASGI · WSGI · Celery   │
└─────────────────────────┬───────────────────────────────┘
                          │
    ┌─────────────────────┼─────────────────────┐
    │                     │                     │
┌───▼─────┐  ┌───────────▼──────────┐  ┌──────▼───────┐
│ tenants │  │      accounts        │  │   billing    │
│ Tenant  │  │ User · Role · Perm   │  │ Plan · Sub   │
│ Settings│  │ Membership · Profile │  │ Invoice      │
│ Middleware  │ Invitation · Auth   │  │ UsageTracker │
└────┬────┘  └───────────┬──────────┘  └──────┬───────┘
     │                   │                     │
     ├───────────────────┼─────────────────────┤
     │                   │                     │
┌────▼────┐  ┌───────────▼──────────┐  ┌──────▼───────┐
│ tickets │  │     contacts         │  │   kanban     │
│ Ticket  │  │ Contact · Company    │  │ Board        │
│ Status  │  │ ContactGroup         │  │ Column       │
│ Queue   │  │                      │  │ CardPosition │
│ SLA     │  │                      │  │              │
└────┬────┘  └──────────────────────┘  └──────────────┘
     │
┌────▼────┐  ┌──────────────────────┐  ┌──────────────┐
│comments │  │    messaging         │  │notifications │
│ Comment │  │ Conversation         │  │ Notification │
│ Mention │  │ Participant          │  │ Preference   │
│ActivityLog│ Message              │  │              │
└─────────┘  └──────────────────────┘  └──────────────┘

┌─────────┐  ┌──────────────────────┐  ┌──────────────┐
│ agents  │  │   custom_fields      │  │ attachments  │
│Availability│ FieldDefinition     │  │ Attachment   │
│          │  │ FieldValue          │  │              │
└─────────┘  └──────────────────────┘  └──────────────┘

┌──────────────────────────────────────────────────────┐
│                    analytics                          │
│ ReportDefinition · DashboardWidget · ExportJob        │
└──────────────────────────────────────────────────────┘
```

### 3.2 Detailed App Responsibilities

| App | Responsibility | Key Models | Tenant-Scoped |
|-----|---------------|------------|---------------|
| **main** | Base models, managers, context, settings, ASGI/WSGI, Celery config | `TimestampedModel`, `TenantScopedModel` | N/A (abstract) |
| **tenants** | Tenant lifecycle, subdomain routing, middleware, settings, frontend views | `Tenant`, `TenantSettings` | `Tenant` is global; `TenantSettings` is linked 1:1 |
| **accounts** | User management, RBAC, multi-tenant membership, invitations | `User`, `Permission`, `Role`, `Profile`, `TenantMembership`, `Invitation` | `Role`, `Profile`, `Invitation` are scoped |
| **billing** | Stripe integration, plan management, subscription lifecycle, usage tracking | `Plan`, `Subscription`, `Invoice`, `UsageTracker` | `Subscription`/`Invoice`/`UsageTracker` linked to tenant |
| **tickets** | Ticket CRUD, custom statuses, queues, SLA policies, escalation rules | `Ticket`, `TicketStatus`, `Queue`, `SLAPolicy`, `EscalationRule`, `TicketAssignment` | All scoped |
| **contacts** | Contact/company CRM, groups/segments | `Contact`, `Company`, `ContactGroup` | All scoped |
| **kanban** | Visual workflow boards with drag-and-drop | `Board`, `Column`, `CardPosition` | `Board`/`Column` scoped |
| **comments** | Threaded comments, @mentions, activity audit log | `Comment`, `Mention`, `ActivityLog` | `Comment`/`ActivityLog` scoped |
| **messaging** | Real-time WebSocket chat (direct, group, ticket) | `Conversation`, `ConversationParticipant`, `Message` | `Conversation`/`Message` scoped |
| **notifications** | Push notifications (WebSocket) + email delivery, preferences | `Notification`, `NotificationPreference` | All scoped |
| **attachments** | Secure file upload with MIME validation, tenant-isolated storage | `Attachment` | Scoped |
| **analytics** | Reports, dashboard widgets, async exports | `ReportDefinition`, `DashboardWidget`, `ExportJob` | All scoped |
| **agents** | Agent availability tracking, workload management | `AgentAvailability` | Scoped |
| **custom_fields** | Tenant-configurable EAV custom fields | `CustomFieldDefinition`, `CustomFieldValue` | All scoped |

---

## 4. Database Schema

### 4.1 Entity-Relationship Summary

```
User (global)
  ├── 1:N TenantMembership → Tenant
  │     └── 1:1 Role (tenant-scoped)
  ├── 1:N Profile (per tenant)
  └── 1:N Invitation (per tenant)

Tenant (global)
  ├── 1:1 TenantSettings
  ├── 1:1 Subscription → Plan
  │     └── 1:N Invoice
  ├── 1:1 UsageTracker
  ├── 1:N Ticket
  │     ├── N:1 TicketStatus
  │     ├── N:1 Queue
  │     ├── N:1 Contact
  │     ├── N:1 Company
  │     ├── N:1 User (assignee)
  │     ├── N:1 User (created_by)
  │     ├── 1:N TicketAssignment
  │     └── 1:N Comment (via GFK)
  ├── 1:N Contact
  │     └── N:1 Company
  ├── 1:N Company
  ├── 1:N ContactGroup ←→ M:N Contact
  ├── 1:N Board → 1:N Column → 1:N CardPosition (GFK)
  ├── 1:N Conversation → M:N User (via Participant)
  │     └── 1:N Message
  ├── 1:N Notification → User
  ├── 1:N Role → M:N Permission (global)
  ├── 1:N CustomFieldDefinition → 1:N CustomFieldValue (GFK)
  ├── 1:N Attachment (GFK)
  ├── 1:N ActivityLog (GFK)
  ├── 1:N SLAPolicy → 1:N EscalationRule
  ├── 1:N AgentAvailability
  ├── 1:N ReportDefinition → 1:N ExportJob
  └── 1:N DashboardWidget
```

### 4.2 Key Indexes

| Table | Index | Purpose |
|-------|-------|---------|
| `tickets_ticket` | `(tenant, status)` | Ticket list filtering |
| `tickets_ticket` | `(tenant, assignee)` | Agent workload queries |
| `tickets_ticket` | `(tenant, priority)` | Priority-based views |
| `tickets_ticket` | `(tenant, created_at)` | Timeline sorting |
| `tickets_ticket` | `(tenant, number)` UNIQUE | Per-tenant ticket numbering |
| `notifications_notification` | `(recipient, is_read, -created_at)` | Unread notification queries |
| `comments_activitylog` | `(tenant, content_type, object_id, created_at)` | Entity history lookup |
| `comments_activitylog` | `(tenant, actor, created_at)` | User activity timeline |
| `messaging_message` | `(conversation, created_at)` | Message chronology |
| `custom_fields_customfieldvalue` | `(tenant, field, value_text)` | Custom field text search |
| `custom_fields_customfieldvalue` | `(tenant, field, value_number)` | Custom field numeric range |
| `custom_fields_customfieldvalue` | `(tenant, field, value_date)` | Custom field date filtering |
| `attachments_attachment` | `(content_type, object_id)` | Polymorphic attachment lookup |

### 4.3 Unique Constraints

| Table | Constraint | Ensures |
|-------|-----------|---------|
| `tickets_ticket` | `(tenant, number)` | Ticket numbers unique per tenant |
| `tickets_ticketstatus` | `(tenant, slug)` | Status slugs unique per tenant |
| `accounts_role` | `(tenant, slug)` | Role slugs unique per tenant |
| `accounts_tenantmembership` | `(user, tenant)` | One membership per user per tenant |
| `accounts_profile` | `(user, tenant)` | One profile per user per tenant |
| `contacts_contact` | `(tenant, email)` | Contact emails unique per tenant |
| `contacts_company` | `(tenant, name)` | Company names unique per tenant |
| `custom_fields_customfielddefinition` | `(tenant, module, slug)` | Field slugs unique per module per tenant |
| `custom_fields_customfieldvalue` | `(field, content_type, object_id)` | One value per field per entity |

---

## 5. Tenant Routing Design

### 5.1 Resolution Flow

```
Incoming Request
       │
       ▼
  Extract Host Header
  (strip port, lowercase)
       │
       ├── "localhost" / "127.0.0.1" / BASE_DOMAIN
       │     └── Main site (request.tenant = None)
       │
       ├── "*.localhost" (e.g. "demo.localhost")
       │     └── Extract slug → DB lookup: Tenant(slug=slug, is_active=True)
       │
       ├── "*.BASE_DOMAIN" (e.g. "demo.example.com")
       │     └── Extract slug → DB lookup: Tenant(slug=slug, is_active=True)
       │           (rejects sub-subdomains like a.b.example.com)
       │
       └── Any other host (e.g. "crm.acme.com")
              └── DB lookup: Tenant(domain=host, is_active=True)
                     (custom domain routing)
```

### 5.2 Exempt Paths

These paths bypass tenant resolution entirely and are served regardless:

```
/admin/              → Django admin
/static/             → Static files (WhiteNoise)
/media/              → Uploaded media
/api/v1/accounts/auth/ → Authentication endpoints
/api/v1/billing/plans/ → Public plan listing
/api/v1/billing/webhook/ → Stripe webhook receiver
/api/docs/           → Swagger API documentation
/api/schema/         → OpenAPI schema
/accounts/           → django-allauth SSO routes
```

### 5.3 Tenant Not Found

When no matching tenant is found, the middleware returns:

```json
HTTP 404 {"detail": "Tenant not found."}
```

### 5.4 Session Cookie Configuration

| Setting | Dev (localhost) | Production |
|---------|-----------------|------------|
| `SESSION_COOKIE_DOMAIN` | `None` (per-host) | `.example.com` (shared across subdomains) |
| `CSRF_COOKIE_DOMAIN` | `None` | `.example.com` |
| `CSRF_TRUSTED_ORIGINS` | `http://localhost:8001`, `http://*.localhost:8001` | `https://*.example.com` |
| `SESSION_ENGINE` | `django.contrib.sessions.backends.cache` | Same (Redis-backed) |

---

## 6. Auth & SSO Flow

### 6.1 Authentication Methods

The platform supports three authentication strategies, configurable per-tenant via `TenantSettings.auth_method`:

| Method | Backend | Token Type | Use Case |
|--------|---------|------------|----------|
| **Django (default)** | Email + password | Session cookie | Frontend web app |
| **SSO** | OAuth2/OIDC via allauth | Session cookie | Enterprise SSO |
| **API** | JWT (SimpleJWT) | Bearer token | API integrations |

### 6.2 Session-Based Auth Flow (Frontend)

```
1. User visits demo.localhost:8001/login/
2. TenantMiddleware sets request.tenant = Demo
3. User submits email + password via POST
4. Django authenticate() verifies credentials
5. django.contrib.auth.login() creates session
6. Session stored in Redis (db3) via cache backend
7. Session cookie set (HttpOnly, SameSite=Lax)
8. Redirect to /dashboard/
```

### 6.3 JWT Auth Flow (API)

```
1. POST /api/v1/accounts/auth/token/ {email, password}
2. SimpleJWT validates credentials
3. Returns {access: "...", refresh: "..."}
4. Client includes "Authorization: Bearer <access>" on subsequent requests
5. Access token lifetime: 15 minutes
6. Refresh token lifetime: 7 days
7. Refresh tokens rotate on use (old ones blacklisted)
```

### 6.4 SSO/OAuth2 Flow

```
1. Tenant admin configures SSO in TenantSettings:
   - sso_provider: google | microsoft | okta | custom
   - sso_client_id, sso_client_secret, sso_authority_url
2. User visits /accounts/google/login/ (or other provider)
3. django-allauth redirects to IdP
4. User authenticates with IdP
5. Callback to /accounts/google/login/callback/
6. allauth creates/links User, creates session
7. Redirect to /dashboard/
```

### 6.5 RBAC (Role-Based Access Control)

```
Permission model (global):
  codename: "resource.action" (e.g., "ticket.create")
  29 permissions across 8 resources

Role model (per-tenant):
  ┌──────────┬───────┬──────────────────────┐
  │ Role     │ Level │ Permissions          │
  ├──────────┼───────┼──────────────────────┤
  │ Admin    │  10   │ All 29 permissions   │
  │ Manager  │  20   │ 22 permissions       │
  │ Agent    │  30   │ 10 permissions       │
  │ Viewer   │  40   │  4 permissions       │
  └──────────┴───────┴──────────────────────┘

TenantMembership:
  Links User → Tenant → Role
  Each user has exactly one role per tenant
```

---

## 7. Custom Fields Architecture

### 7.1 Design Pattern: Typed EAV (Entity-Attribute-Value)

The custom fields system uses a hybrid approach that combines:
- **EAV pattern** for flexible, tenant-defined schemas
- **Typed value columns** for efficient querying and indexing
- **JSONField** on entities for denormalised fast reads

### 7.2 Schema

```
CustomFieldDefinition (tenant-scoped)
  ├── module: ticket | contact | company
  ├── name: "Customer Tier"
  ├── slug: "customer_tier"
  ├── field_type: text | textarea | number | date | select | multiselect | checkbox | file
  ├── options: [{"value": "gold", "label": "Gold"}, ...]  (for select/multiselect)
  ├── is_required: bool
  ├── default_value: JSON
  ├── validation_rules: {"min": 0, "max": 100, "regex": "..."}
  ├── order: int (display ordering)
  ├── visible_to_roles: M:N → Role (empty = all roles)
  └── is_active: bool (soft disable)

CustomFieldValue (tenant-scoped)
  ├── field → CustomFieldDefinition
  ├── content_type + object_id → GFK (any entity)
  ├── value_text: TEXT      (text, textarea, select, multiselect, file)
  ├── value_number: DECIMAL  (number fields)
  ├── value_date: DATETIME   (date fields)
  └── value_bool: BOOLEAN    (checkbox fields)
```

### 7.3 Query Strategy

```
# Find all tickets where custom field "customer_tier" = "gold":
CustomFieldValue.objects.filter(
    field__slug="customer_tier",
    field__module="ticket",
    value_text="gold",
).values_list("object_id", flat=True)

# Then filter tickets:
Ticket.objects.filter(id__in=above_queryset)
```

### 7.4 Denormalisation

Each entity (Ticket, Contact, Company) also carries a `custom_data` JSONField:

```json
{
  "customer_tier": "gold",
  "contract_value": 50000,
  "renewal_date": "2026-06-15"
}
```

This provides fast reads without joins. The `CustomFieldValue` table is the source of truth for queries and reporting.

### 7.5 Role-Based Visibility

Custom fields can be restricted to specific roles via the `visible_to_roles` M:N relationship. An empty relationship means the field is visible to all roles. This allows tenant admins to create fields that only managers or admins can see (e.g., internal cost tracking).

---

## 8. Stripe Billing Flow

### 8.1 Data Model

```
Plan (global)
  ├── tier: free | pro | enterprise
  ├── stripe_product_id: "prod_..."
  ├── stripe_price_monthly / stripe_price_yearly
  ├── price_monthly / price_yearly (display amounts)
  ├── Limits: max_users, max_contacts, max_tickets_per_month, max_storage_mb, max_custom_fields
  └── Feature flags: has_api_access, has_realtime, has_custom_roles, has_sso, has_sla_management

Subscription (per-tenant, 1:1)
  ├── tenant → Tenant
  ├── plan → Plan
  ├── stripe_subscription_id, stripe_customer_id
  ├── status: trialing | active | past_due | canceled | incomplete | unpaid
  ├── billing_cycle: monthly | yearly
  ├── current_period_start / current_period_end
  ├── cancel_at_period_end, canceled_at, trial_end
  └── Properties: is_active, in_grace_period

Invoice (per-subscription, 1:N)
  ├── stripe_invoice_id, amount, currency, status
  ├── invoice_pdf_url, hosted_invoice_url
  └── period_start / period_end

UsageTracker (per-tenant, 1:1)
  ├── period_start
  ├── contacts_count, tickets_created
  ├── storage_used_mb, api_calls
  └── updated_at
```

### 8.2 Checkout Flow

```
1. User visits /billing/ page
2. Frontend loads plans from GET /api/v1/billing/plans/
3. User selects plan and clicks "Subscribe"
4. Frontend calls POST /api/v1/billing/checkout/
   → Backend creates Stripe Checkout Session
   → Returns checkout URL
5. User redirected to Stripe Checkout
6. User completes payment
7. Stripe fires webhook → POST /api/v1/billing/webhook/
8. Backend processes checkout.session.completed:
   → Creates/updates Subscription
   → Creates Invoice record
9. User redirected back to /billing/ (success)
```

### 8.3 Webhook Processing

```
Webhook events handled:
  checkout.session.completed   → Create Subscription + Invoice
  invoice.paid                 → Update Subscription period, create Invoice
  invoice.payment_failed       → Set Subscription status = past_due
  customer.subscription.updated → Sync Subscription fields
  customer.subscription.deleted → Set Subscription status = canceled
```

### 8.4 Subscription Enforcement (Middleware)

```python
class SubscriptionMiddleware:
    """Runs AFTER TenantMiddleware for every request."""

    def __call__(self, request):
        # Skip exempt paths (admin, static, auth, webhook, billing)
        # Skip requests without tenant context (main site)
        # Skip tenants without subscription (free tier)
        # Allow active or trialing subscriptions
        # Allow past_due within 7-day grace window
        # BLOCK all others → HTTP 402 Payment Required
```

### 8.5 Plan Limit Enforcement

| Limit | Enforcement Point | Behaviour |
|-------|-------------------|-----------|
| `max_users` | User invite/creation | Reject with 403 if at capacity |
| `max_contacts` | Contact creation | Reject with 403 |
| `max_tickets_per_month` | Ticket creation | Reject with 403 |
| `max_storage_mb` | File upload | Reject with 413 |
| `max_custom_fields` | Field definition creation | Reject with 403 |
| Feature flags | View/serializer permissions | Hide/disable features |

---

## 9. Security Threat Model

### 9.1 OWASP Top 10 Alignment

| # | Threat | Mitigation |
|---|--------|-----------|
| A01 | **Broken Access Control** | Row-level tenant isolation via `TenantAwareManager` auto-filtering; RBAC with 29 granular permissions; UUID PKs prevent enumeration |
| A02 | **Cryptographic Failures** | Django's password hashing (PBKDF2-SHA256); HTTPS enforced in production; JWT signing with separate secret; SSO client secrets encrypted at app level |
| A03 | **Injection** | Django ORM prevents SQL injection; template auto-escaping prevents XSS; DRF serializer validation on all inputs |
| A04 | **Insecure Design** | Tenant context cleared in `finally` block; `unscoped` manager requires explicit use; webhook signature verification |
| A05 | **Security Misconfiguration** | `SecurityMiddleware` enabled; `X-Frame-Options: DENY`; `X-Content-Type-Options: nosniff`; HSTS in production; `SESSION_COOKIE_HTTPONLY = True` |
| A06 | **Vulnerable Components** | Pinned dependency versions; WhiteNoise for static files (no directory traversal) |
| A07 | **Authentication Failures** | Rate limiting on auth endpoints (10/min); JWT access tokens expire in 15 minutes; refresh token rotation with blacklisting |
| A08 | **Data Integrity Failures** | Stripe webhook signature verification; CSRF protection on all state-changing requests; `SameSite=Lax` cookies |
| A09 | **Logging & Monitoring** | `ActivityLog` immutable audit trail for all entity changes; structured logging with tenant context; IP address recording |
| A10 | **SSRF** | No user-controlled URL fetching; Stripe calls use official SDK |

### 9.2 Tenant Isolation Guarantees

| Layer | Mechanism | Failure Mode |
|-------|-----------|-------------|
| **Network** | Subdomain routing, separate cookies per tenant | Cookie leakage between subdomains mitigated by `SameSite=Lax` |
| **Application** | `TenantAwareManager` auto-filters ALL queries | Bypass only possible via explicit `Model.unscoped` (auditable) |
| **Write** | `TenantScopedModel.save()` auto-sets tenant | Cross-tenant write requires explicit tenant assignment |
| **File Storage** | Tenant-scoped upload paths: `tenants/<id>/attachments/...` | Direct URL access controlled by application-level auth |
| **Session** | Redis-backed sessions keyed by session ID | Session cookies domain-scoped; no cross-tenant session leakage |
| **API** | DRF `IsAuthenticated` default; JWT + Session auth | Unauthenticated requests blocked; API responses auto-filtered |

### 9.3 File Upload Security

| Control | Implementation |
|---------|---------------|
| MIME type validation | Server-side via `python-magic` (not client-supplied Content-Type) |
| File size limit | 25MB max (`FILE_UPLOAD_MAX_MEMORY_SIZE`) |
| Storage isolation | `tenants/<tenant_id>/attachments/YYYY/MM/<filename>` |
| Filename sanitisation | Django's `upload_to` callable generates safe paths |

### 9.4 Rate Limiting

| Scope | Limit | Applies To |
|-------|-------|-----------|
| `auth` | 10/min | Login, registration, token endpoints |
| `api_default` | 200/min | Standard API endpoints |
| `api_heavy` | 30/min | Export, analytics, bulk operations |
| `webhook` | 60/min | Stripe webhook receiver |

---

## 10. Tool & Library Recommendations

### 10.1 Current Stack

| Category | Tool | Version | Purpose |
|----------|------|---------|---------|
| **Runtime** | Python | 3.12.3 | Application runtime |
| **Framework** | Django | 6.0.2 | Web framework |
| **API** | Django REST Framework | 3.16.1 | REST API |
| **Auth (JWT)** | SimpleJWT | 5.4.x | JWT token management |
| **Auth (SSO)** | django-allauth | 65.x | OAuth2/OIDC/Social auth |
| **WebSocket** | Django Channels | 4.x | Real-time WebSocket |
| **Task Queue** | Celery | 5.4.x | Background jobs |
| **Cache/Broker** | Redis | 7.x | Caching, Celery broker, Channels layer |
| **API Docs** | drf-spectacular | 0.28.x | OpenAPI 3.0 schema + Swagger UI |
| **CORS** | django-cors-headers | 4.x | Cross-origin resource sharing |
| **Filtering** | django-filter | 24.x | DRF queryset filtering |
| **Static Files** | WhiteNoise | 6.x | Static file serving |
| **Config** | django-environ | 0.12.x | Environment variable management |
| **Monitoring** | Flower | 2.0 | Celery task monitoring |
| **Process Mgmt** | PM2 | 5.x | Process management |
| **Frontend** | Bootstrap | 5.3.3 | UI framework (CDN) |

### 10.2 Recommended Additions for Production

| Category | Tool | Purpose |
|----------|------|---------|
| **Database** | PostgreSQL 16 | Production-grade RDBMS (replace SQLite) |
| **Search** | Elasticsearch / Meilisearch | Full-text search across tickets, contacts |
| **File Storage** | AWS S3 / MinIO | Scalable, durable file storage |
| **Email** | Amazon SES / SendGrid | Transactional email delivery |
| **Monitoring** | Sentry | Error tracking and performance monitoring |
| **APM** | New Relic / Datadog | Application performance monitoring |
| **Logging** | ELK Stack / Loki | Centralised log aggregation |
| **CI/CD** | GitHub Actions | Automated testing and deployment |
| **Secrets** | HashiCorp Vault / AWS Secrets Manager | Production secret management |
| **CDN** | CloudFront / Cloudflare | Static asset delivery, DDoS protection |
| **Load Testing** | Locust / k6 | Performance and capacity testing |
| **Backup** | pg_dump + S3 | Automated database backups |

---

## 11. Development Roadmap

### Phase 1: MVP (Current — Complete)

- [x] 13 Django app architecture with models, views, serializers
- [x] Row-level multi-tenancy with automatic scoping
- [x] Subdomain-based tenant routing
- [x] Session + JWT authentication
- [x] RBAC with 4 default roles and 29 permissions
- [x] Ticket system with custom statuses, queues, SLA policies
- [x] Contact/Company CRM with groups
- [x] Kanban boards with drag-and-drop
- [x] Real-time messaging (WebSocket)
- [x] Notification system (WebSocket + email)
- [x] Threaded comments with @mentions
- [x] File attachments with MIME validation
- [x] Stripe billing integration (plans, subscriptions, invoices)
- [x] Custom fields (EAV pattern)
- [x] Analytics (reports, dashboard widgets, exports)
- [x] Agent availability tracking
- [x] Activity audit log
- [x] Bootstrap 5.3 frontend with responsive design
- [x] API documentation (Swagger UI)
- [x] PM2 process management

### Phase 2: Polish (Next)

- [ ] Full test suite (unit, integration, E2E)
- [ ] Custom field form rendering in ticket/contact creation
- [ ] Email notification delivery (configure SMTP)
- [ ] Ticket detail activity timeline (display ActivityLog)
- [ ] Search functionality (full-text across tickets, contacts)
- [ ] User profile management UI
- [ ] Invitation flow UI (send, accept, manage)
- [ ] Dashboard widgets with chart visualisation (Chart.js)
- [ ] Ticket export functionality (CSV, XLSX)
- [ ] Mobile-responsive layout optimisation

### Phase 3: Hardening

- [ ] Migrate to PostgreSQL
- [ ] Add database connection pooling (PgBouncer)
- [ ] Implement full Celery task suite (SLA monitoring, usage tracking, cleanup)
- [ ] Add Sentry error tracking
- [ ] HTTPS with Let's Encrypt certificates
- [ ] Automated database backups
- [ ] Rate limiting per-tenant (not just per-IP)
- [ ] Input sanitisation audit (XSS, injection)
- [ ] CORS policy tightening for production domains
- [ ] Security headers audit (CSP, HSTS preload)

### Phase 4: Scale & Production

- [ ] Migrate to S3/MinIO for file storage
- [ ] Add Elasticsearch for full-text search
- [ ] Implement horizontal scaling (multiple Gunicorn instances)
- [ ] Add Redis Sentinel or Cluster for HA
- [ ] Implement read replicas for analytics queries
- [ ] Add CDN for static assets
- [ ] Performance profiling and query optimisation
- [ ] Load testing with k6/Locust
- [ ] CI/CD pipeline (GitHub Actions)
- [ ] Automated deployment with zero-downtime rolling updates

---

## 12. Common SaaS Failure Avoidance

### 12.1 Failure: Data Leakage Between Tenants

**How other platforms fail:** Forgetting to filter by tenant in a query, allowing users to see other tenants' data through API endpoints or search results.

**How Kanzen Suite prevents this:**
- `TenantAwareManager` is the **default** manager on all tenant-scoped models — every `Model.objects.all()` call is automatically filtered
- Thread-local tenant context is set by middleware on every request and cleared in a `finally` block
- `TenantScopedModel.save()` auto-assigns tenant on write, with `ValueError` if no context exists
- The `unscoped` manager exists for admin queries but requires explicit opt-in
- UUID primary keys prevent sequential ID enumeration

### 12.2 Failure: Subscription Bypass

**How other platforms fail:** Users continue using premium features after subscription lapses by bookmarking direct URLs or using cached sessions.

**How Kanzen Suite prevents this:**
- `SubscriptionMiddleware` runs on **every request** (after `TenantMiddleware`)
- Checks subscription status on every page load / API call
- 7-day grace period for `past_due` status before hard lockout
- Returns HTTP 402 with clear messaging when blocked
- Exempt paths are minimal and explicitly listed

### 12.3 Failure: Broken Tenant Isolation in File Storage

**How other platforms fail:** Storing all tenant files in a flat directory, allowing path traversal or enumeration attacks.

**How Kanzen Suite prevents this:**
- Files stored in tenant-scoped paths: `tenants/<tenant_uuid>/attachments/YYYY/MM/<filename>`
- UUID-based tenant IDs prevent enumeration
- MIME type validated server-side via `python-magic` (not client-supplied `Content-Type`)
- File size limits enforced (25MB)
- File access controlled by application authentication (not direct URL access in production)

### 12.4 Failure: Noisy Neighbour / Resource Exhaustion

**How other platforms fail:** One tenant's heavy usage (API calls, file uploads, ticket creation) degrades performance for all tenants.

**How Kanzen Suite prevents this:**
- Per-tenant `UsageTracker` monitors contacts, tickets, storage, API calls
- Plan-level limits enforce ceilings: `max_users`, `max_contacts`, `max_tickets_per_month`, `max_storage_mb`
- DRF rate limiting at the API layer (200/min default, 30/min heavy)
- Celery task queues are shared but separate from the web server

### 12.5 Failure: Session/Cookie Leakage Between Subdomains

**How other platforms fail:** Setting `SESSION_COOKIE_DOMAIN = ".example.com"` allows a session cookie set by `evil.example.com` to be read by `victim.example.com`.

**How Kanzen Suite prevents this:**
- In development: `SESSION_COOKIE_DOMAIN = None` (per-host cookies)
- `SESSION_COOKIE_HTTPONLY = True` (no JavaScript access)
- `SESSION_COOKIE_SAMESITE = "Lax"` (prevents CSRF via cross-site POST)
- Each tenant's data is further isolated by the `TenantAwareManager` — even if a session cookie were somehow shared, the tenant context comes from the URL host, not the session
- CSRF tokens are tied to the session, adding a second verification layer

### 12.6 Failure: Accidental Cross-Tenant Writes

**How other platforms fail:** A bug in the API allows creating a ticket in tenant A while authenticated in tenant B.

**How Kanzen Suite prevents this:**
- `TenantScopedModel.save()` reads tenant from thread-local context (set by middleware from the request URL)
- The tenant on a new object is always the current request's tenant, regardless of what the API consumer sends
- The `tenant` field is `editable=False`, so it cannot be set via serializer input
- DRF serializers don't expose `tenant` as a writable field

### 12.7 Failure: Webhook Replay/Forgery Attacks

**How other platforms fail:** Not verifying webhook signatures, allowing attackers to forge billing events and grant themselves premium subscriptions.

**How Kanzen Suite prevents this:**
- Stripe webhook endpoint verifies signature using `STRIPE_WEBHOOK_SECRET`
- Webhook path (`/api/v1/billing/webhook/`) is exempt from tenant resolution (no tenant context needed)
- Webhook path is exempt from CSRF protection (Stripe sends POST without session)
- Rate limited to 60/min to prevent amplification attacks

### 12.8 Failure: Role Escalation

**How other platforms fail:** Users can modify their own role or create roles with higher privileges than their own.

**How Kanzen Suite prevents this:**
- System roles (`is_system=True`) cannot be deleted or modified by tenants
- Role `hierarchy_level` enforces ordering (Admin=10 > Manager=20 > Agent=30 > Viewer=40)
- Lower hierarchy_level = higher authority
- Role CRUD operations should validate that the requesting user's role has a lower (more privileged) hierarchy_level than the target role

### 12.9 Failure: Stale Cache Serving Wrong Tenant's Data

**How other platforms fail:** Caching query results without including the tenant context in the cache key, serving tenant A's data to tenant B.

**How Kanzen Suite prevents this:**
- Redis cache key prefix: `epstein` (application-level)
- Cache backend is used for sessions and general caching, not for model-level query caching
- The `TenantAwareManager` runs a fresh query per request — no implicit queryset caching
- If application-level caching is added, keys must include the tenant ID

### 12.10 Failure: Migration Disasters in Multi-Tenant Systems

**How other platforms fail:** Schema-per-tenant architectures require running migrations on hundreds of schemas, with failures leaving some tenants on old schemas.

**How Kanzen Suite prevents this:**
- **Shared-schema architecture** means one migration applies to all tenants simultaneously
- No per-tenant database schemas or databases to manage
- Standard Django `migrate` command handles everything
- Rollback is a single operation, not per-tenant

---

## Appendix A: API Endpoint Map

| Prefix | App | Key Endpoints |
|--------|-----|--------------|
| `/api/v1/tenants/` | tenants | Tenant CRUD, settings |
| `/api/v1/accounts/` | accounts | Users, roles, permissions, memberships, auth (login/register/token) |
| `/api/v1/tickets/` | tickets | Tickets, statuses, queues, SLA policies, assignments |
| `/api/v1/contacts/` | contacts | Contacts, companies, groups |
| `/api/v1/billing/` | billing | Plans, subscription, checkout, webhook, invoices |
| `/api/v1/kanban/` | kanban | Boards, columns, card positions |
| `/api/v1/comments/` | comments | Comments, mentions, activity logs |
| `/api/v1/messaging/` | messaging | Conversations, messages |
| `/api/v1/notifications/` | notifications | Notifications, preferences |
| `/api/v1/attachments/` | attachments | File upload/download |
| `/api/v1/analytics/` | analytics | Reports, widgets, export jobs |
| `/api/v1/agents/` | agents | Agent availability |
| `/api/v1/custom-fields/` | custom_fields | Field definitions, values |
| `/api/docs/` | — | Swagger UI |
| `/api/schema/` | — | OpenAPI 3.0 schema |

## Appendix B: WebSocket Endpoints

| Path | Consumer | Purpose |
|------|----------|---------|
| `ws/chat/<conversation_id>/` | `ChatConsumer` | Real-time messaging within a conversation |
| `ws/notifications/` | `NotificationConsumer` | Real-time push notifications for authenticated user |

## Appendix C: Celery Task Queues

| Queue | Purpose |
|-------|---------|
| `kanzan_default` | General background tasks |
| `kanzan_email` | Email delivery (notifications, invitations) |
| `kanzan_webhooks` | Webhook processing (Stripe, external integrations) |

## Appendix D: Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `DJANGO_SECRET_KEY` | Django secret key | (required) |
| `DJANGO_DEBUG` | Debug mode | `True` |
| `DATABASE_URL` | Database connection string | `sqlite:///db.sqlite3` |
| `REDIS_URL` | Redis connection URL | `redis://127.0.0.1:6379` |
| `BASE_DOMAIN` | Base domain for subdomain routing | `localhost` |
| `STRIPE_SECRET_KEY` | Stripe API secret key | (empty) |
| `STRIPE_PUBLISHABLE_KEY` | Stripe publishable key | (empty) |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook signing secret | (empty) |
| `JWT_SECRET_KEY` | JWT signing key | `SECRET_KEY` |
| `EMAIL_HOST` | SMTP server host | `smtp.gmail.com` |
| `EMAIL_HOST_USER` | SMTP username | (empty) |
| `EMAIL_HOST_PASSWORD` | SMTP password | (empty) |
| `EMAIL_PORT` | SMTP port | `587` |
| `EMAIL_USE_TLS` | Use TLS for SMTP | `True` |
| `DEFAULT_FROM_EMAIL` | Default sender email | `noreply@kanzan.local` |

## Appendix E: Default Credentials (Development Only)

| Resource | Credential |
|----------|-----------|
| Superuser email | `admin@kanzan.local` |
| Superuser password | `Pl@nC-ICT_2024` |
| Demo tenant slug | `demo` |
| Demo tenant URL | `http://demo.localhost:8001/` |
| Flower auth | `admin:changeme` |
| Django admin | `http://localhost:8001/admin/` |
