# CRM — Project Intelligence

> **Last refreshed: 2026-07-27 (clean-room deep dive — docs detached).** A 10-agent background
> workflow (8 subsystem readers + 2 adversarial verifiers, ~1.9M tokens) re-derived this file from
> **source only**, treating the prior CLAUDE.md/MEMORY.md as untrusted. Backed by live
> Django-shell/DB probes, full `pytest`, every quality gate, and hand spot-checks of the
> highest-severity findings. Detailed backing store: **`docs/audits/2026-07-27-deep-dive/`**
> (00-OVERVIEW · 01-SECURITY-FINDINGS · 02-SUBSYSTEM-REFERENCE).

## ⚠️⚠️ READ FIRST — this working tree is BEHIND origin and PRE-security-hardening

| | |
|---|---|
| Local branch / HEAD | `main` @ **`df9b29d`** "new updates" (2026-07-09) |
| `origin/main` | **`6e035de`** (2026-07-22) — local HEAD is an **ancestor**, i.e. **5 commits behind** |
| Working tree | **4 dirty files, ALL frontend** → **backend `.py` == `df9b29d` exactly** |

**The 5 origin commits ABSENT on disk are the entire security-hardening batch:** `73dfef1` (bulk-action
authz + go-live hardening), `b37b3be` (zero-perm RBAC seeding fix), `7c7b3de` (tenant-membership gates +
RBAC guards + XSS sanitization), `417d9cf` (freezegun), `6e035de` (merge). **Therefore every security fix
that older docs/memory call "FIXED" is NOT in this checkout — the pre-hardening bugs are live on disk.**

**DB-vs-code smoking gun:** `db.sqlite3` has migration `accounts/0013_seed_full_permission_catalogue`
**applied**, but that file is **absent on disk** (`apps/accounts/migrations/` stops at `0012`) — proof the
DB was migrated by newer code than this checkout. So the DB *looks* correctly seeded (all 9 tenants have real
per-role perms) while the on-disk seeding code still produces **0-permission roles** for any freshly-provisioned
tenant (reproduced live twice). **Never use the live DB as an oracle for on-disk-code behavior.**

### 🔴 Open security holes ON THIS CHECKOUT (all verified from source; details in `01-SECURITY-FINDINGS.md`)
Root cause of most: the **JWT `tenant_id` claim is written at issuance but never read/enforced**
(`accounts/serializers.py:378` vs no reader; DRF uses stock SimpleJWT). `request.tenant` comes purely from the
**Host header**, so *any valid JWT works against any tenant's subdomain* — the only barrier is a per-view
membership check. `HasTenantPermission`/`IsTenantMember` are SOUND (reject `membership is None`); the holes are
viewsets that use bare `[IsAuthenticated]` and scope only by the Host-controlled tenant contextvar.

| # | Finding | file:line |
|---|---------|-----------|
| C1 | Cross-tenant READ + WRITE of comments & audit log (bare auth; row-filter+internal-hide skipped when `membership is None`) | `comments/views.py:187,343` |
| C2 | Unauthenticated cross-tenant KB file download (`tenants/knowledge/` public media prefix) | `attachments/media_views.py:26,94` + `knowledge/models.py:83` |
| C3 | Prod boots `DEBUG=True` + live Gmail SMTP creds (on-disk `.env`) | `.env` + `settings/__init__.py:12` |
| C4 | Zero-perm RBAC seeding → self-service tenants run on the coarse hierarchy floor | `tenants/signals.py:95` |
| C5 | Manager → Admin vertical escalation (`role` writable, no level guard) | `accounts/views.py:158-167` + `serializers.py:151,172` |
| H1 | Messaging TICKET-conversation cross-tenant participant injection → cross-tenant notification push | `messaging/serializers.py:413-417,598-607` |
| H2 | `CallEventConsumer` accepts foreign JWTs, tenant-wide call group | `voip/consumers.py:30-51` |
| H3 | Reminder `bulk_action` IDOR (row bypass + reassign to any global user) | `crm/views.py:665,694` |
| H4 | `UserViewSet`/`UserGroupViewSet` cross-tenant member/group enumeration (list/retrieve → bare auth) | `accounts/views.py:69-81,459-468` |
| M1-M4 | DashboardView analytics leak · CannedResponse/Macro/SavedView bare auth · KBSearchView · ProfileViewSet foreign-profile create | see 01-SECURITY-FINDINGS |

**REFUTED here (don't carry):** stored-XSS via inbound-email subject/body (all render paths escape via
textContent/DOMPurify) — *but* `knowledge/views.py::preview_file` DOCX regex-sanitization IS a credible XSS
vector, so the refutation is scoped; `grant_temp_role` Manager→Admin (it's admin-gated + refuses `admin`; the
real escalation is C5); `HasTenantPermission` fail-open (it's sound).

### Quality gates (re-run 2026-07-27 — all green except ruff non-blocking)
`pytest -q` = **1078 passed / 22 skipped / 1 xfailed** (210s SQLite) · `makemigrations --check` clean ·
`manage.py check` clean · `scripts/check_theme.py` OK (**126** live hex literals; baseline JSON **145/10**,
stale-high) · `ruff check .` = **197** (non-blocking, pre-existing). CI runs the same gates on PG16+Redis7.

---

## Project Overview

Multi-tenant CRM + Ticketing + Knowledge Base + VoIP SaaS. **Django 6.0.2 · DRF ≥3.16 · Channels 4 ·
Celery 5.4** with Bootstrap 5.3.3 + vanilla JS (SIP.js softphone, TipTap editor, DOMPurify). Row-level
multi-tenancy via subdomain routing + **contextvars** tenant binding (async-safe). Admin is jazzmin-skinned,
superuser-locked. PM2 process management. CI exists.

- **Port** 8001 (ASGI, gunicorn + uvicorn worker) · **Dev DB** SQLite · **Prod DB** PostgreSQL
- **Redis:** db3 (cache + `cached_db` sessions, prefix `crm`), db4 (Celery broker; result backend `django-db`), db5 (Channels, prefix `crm:channels`)
- **SMTP in-process server** 2525 (`crm-smtp`, prod only) · **Flower** 5556 · **TIME_ZONE** `Asia/Kuala_Lumpur` (⚠ `CELERY_TIMEZONE="UTC"` → crontab beats fire on UTC wall-clock; `USE_TZ=True`)
- Python 3.12; venv `.venv/` (`env` is a symlink → `.venv`).

### Quick Reference (dev / localhost — from prior docs, unverified this pass)
```
Superuser:    admin@crm.local / Pl@nC-ICT_2024
Django Admin: http://localhost:8001/admin/   (jazzmin darkly; is_superuser-locked)
Tenant:       http://straat-x.localhost:8001
Flower:       http://localhost:5556 (admin:changeme — CRM_FLOWER_AUTH, read only by PM2 configs)
API Docs:     http://straat-x.localhost:8001/api/docs/
```

## Project Structure (top level)
```
apps/            21 INSTALLED_APPS + nav (URL-only, no models) + __init__.py
main/            Django project: settings/{__init__,base,dev,prod}, checks, admin, celery, asgi, context, models, managers, urls
templates/       48 .html (18 pages/ subfolders + 8 root files); base.html; includes/ (6, 1 orphan); landing/landing_crm.html (rendered landing)
static/          css/custom-v15.css (loaded; 25,528 LOC dirty) · css/custom.css (NOT loaded, theme-check allowlisted) · js/ (14 modules, 6006 LOC)
tests/           69 root test_*.py (+ 7 app-level = 76) · conftest.py 343 LOC · pytest.ini
docs/            architecture.md (STALE) · reference/ (4 STALE surface docs) · qa-audit-2026-06-14/ (11) · testing/ (2) · deploy/ · audits/2026-07-27-deep-dive/ (THIS pass)
requirements/    base.txt · dev.txt · prod.txt (=base). requirements.txt == base.txt (byte-identical)
ecosystem.config.js (prod PM2, 5 procs) · ecosystem.dev.config.js (dev, 4) · Makefile · .env / .env.example
db.sqlite3 · celerybeat-schedule (shelve) · .coverage (all gitignored runtime artifacts)
```

## Registry counts (measured 2026-07-27)
- **91** first-party models across **21** apps · **122** migrations · **46** INSTALLED_APPS · **14** middleware
- **12** Beat entries · **27** `@shared_task` (15 unscheduled) · **43** signal receivers · **76** admin registrations
- **8** management commands · **17** exempt path prefixes · **6** WS consumers
- **36** frontend routes · **29** `main/urls.py` path() · **23** `/api/v1/` includes (22 unique URLConfs; `inbound-email/` dual-mounts as `emails/`)
- **48** templates · **14** JS files/6006 LOC · **76** test modules
- **69** permission codenames (DB has **71** rows — orphans `ticket_category.view`, `ticket_status.view`) · `ROLE_DEFINITIONS`=**6** · ACTION_MAP 76 entries/75 keys (dup `mark_all_read`) · palette **24** keys
- Enums: NotificationType **21** · INTERNAL_ONLY_TYPES **6** · ActivityLog.Action **34** (10 `email_*`) · TicketActivity.Event **27** (inner class `Event`) · HubEmail **9** states/**4** priorities (no medium) · InboundEmail.Status **9** · Reminder.Priority **4** (HAS medium) · CallLog.Status **9** · Webhook.EventType **8** · custom_fields FieldType **8** × ModuleType **3**
- **5** polymorphic GenericFK models (Attachment, Comment, ActivityLog, CustomFieldValue, CardPosition — NOT Notification) · **2** abstract bases (TimestampedModel, TenantScopedModel)

## Apps (21 with models.py)
`accounts` (User + 7-role RBAC + temp-role/perms + memberships + UserGroup + middleware) · `agents`
(AgentAvailability + presence + load-fair pick_email_agent) · `analytics` (dashboard/reports/exports/calendar)
· `api_keys` (SHA-512 keys + auth) · `attachments` (polymorphic uploads + object authz + authed /media/) ·
`billing` (Stripe plans/subscriptions/webhooks) · `comments` (Comment + Mention + ActivityLog + live signals)
· `contacts` (Contact/Company/Account/Group/Event + context.py) · `crm` (Activity + Reminder + scoring +
fire_due_reminders) · `custom_fields` (EAV per tenant + sync signals) · `inbound_email`
(SMTP+IMAP ingestion + seam + agent inbox handoff) · `inbox_hub` (email-triage workspace: 8 models, engine,
state machine, cockpit) · `kanban` (boards/columns/polymorphic CardPosition) · `knowledge` (KB + PG FTS) ·
`messaging` (real-time conversations) · `nav` (URL-only badge counts) · `newsfeed` (announcements) · `notes`
(per-user sticky notes) · `notifications` (in-app/email/WS) · `tenants` (Tenant + middleware + frontend + live
layer + palette) · `tickets` (core ticketing + SLA + kanban sync + webhooks) · `voip` (Asterisk ARI + softphone).

---

## Multi-Tenancy Architecture (3-layer isolation)

1. **TenantMiddleware** (`apps/tenants/middleware.py`): resolves tenant from subdomain
   (`{slug}.localhost` / `{slug}.{BASE_DOMAIN}`) or `TenantSettings.domain`. `_extract_slug` rejects nested
   sub-subdomains. Sets `request.tenant` + binds context; clears in `finally`. **`EXEMPT_PATH_PREFIXES` = 17**
   (incl. dead `/inbound/email/`). **`/admin/` NOT exempt** — dedicated branch defers to
   `SuperuserOnlyAdminSite`. Unresolved real host → `JsonResponse(404)`. `WebSocketTenantMiddleware` mirrors
   from the WS Host header. ⚠ `request.tenant` is Host-derived; the JWT `tenant_id` claim is never enforced.
2. **TenantAwareManager** (`main/managers.py`): default `objects` auto-filters by `get_current_tenant()`;
   **fail-closed** `.none()` when no tenant in context. `Model.unscoped` for cross-tenant. `SoftDeleteTenantManager`
   adds `is_deleted=False`.
3. **TenantScopedModel** (`main/models.py`): abstract, UUID PK + `tenant` FK (CASCADE, editable=False, indexed);
   `save()` auto-assigns tenant, else **raises `ValueError`**.

**Async context** (`main/context.py`): single `contextvars.ContextVar`. `tenant_context(tenant)` is the
nesting-safe form (snapshots+restores); `clear_current_tenant()` hard-sets None (NOT nesting-safe — fine only in
the per-request middleware `finally`).

**Admin** (`main/admin.py`): `SuperuserOnlyAdminSite` reassigns `admin.site.__class__` at import
(`has_permission = is_active AND is_superuser`; jazzmin doesn't override it). `TenantFilteredAdmin` mixin uses
`unscoped` + tenant filter. Registers 0 models here (per-app admin registers 76).

**Palette** (`apps/tenants/colors.py::derive_palette`): returns **24** CSS-var keys (10-step primary scale +
hover/active/etc. + accent + WCAG text-on colors). ⚠ `TenantSettings` field defaults are **`#6366F1`/`#F59E0B`**
(indigo/amber) while `colors.py` code defaults are `#C1121F`/`#E11D2D` (crimson) → a freshly-seeded tenant
renders indigo until edited. `accent_hover` aliased to raw `primary_hex`.

## RBAC

**Hierarchy:** Admin **10** → Manager **20** → Team Lead **25** → Agent **30** / IT **30** / HR **30** → Viewer **40**.
7 system roles seeded on `Tenant.post_save`. `PERMISSION_DEFINITIONS`/`ALL_CODENAMES` = **69** codenames;
`ROLE_DEFINITIONS` = **6** (Viewer permission-less → leans on ≤40 view-fallback). Correct-per-role code grants:
admin 69 / manager 59 / team-lead 40 / agent 24 / it 25 / hr 25.

🔴 **Zero-perm seeding bug (C4, PRESENT on disk):** `_assign_default_role_permissions` (`tenants/signals.py:95`)
resolves roles via tenant-scoped `Role.objects.get` while running with no tenant in context → `.none()` →
`Role.DoesNotExist` → dedicated `except … continue` → all roles seeded 0-perm. The correct `unscoped` impl
`defaults.provision_default_roles` exists but is DEAD. Fix = `Role.unscoped.get` (or `tenant_context`).

**`HasTenantPermission`** (`accounts/permissions.py`, SOUND): `tenant None`→False; `membership None`→False;
no `permission_resource` on view→allow; unmapped action→deny. Explicit perms first
(`get_effective_permissions_qs().exists()`), else **hierarchy fallback** (`view≤40`, `create/update≤30`,
`delete/manage/assign/export≤20`) — this fallback is the "floor" that C4 leaves exposed. `IsTenantMember`
returns True when `tenant is None` (fail-open; mostly moot). Always use **`TenantMembership.effective_role`**
(temp role wins until expiry; non-empty `temporary_permissions` = intersection).

⚠ **Raw-`role` drift at 10 runtime sites** (should be `effective_role`): 4 user-facing routing/visibility
(`agents/services.py:226`, `inbox_hub/assignment.py:246`, `tickets/views.py:1052`/`:1115`) + 6 recipient/actor
selection (`tickets/tasks.py:165,329`, `knowledge/tasks.py:55`, `knowledge/views.py:223`, `crm/tasks.py:279`
[dead task], `inbound_email/services.py:234`). Plus the JWT `role` claim = raw slug.

## Shared visibility helpers
- **`apps/tickets/access.py`** — `agent_visible_tickets_q(user)` = **`Q(assignee=user)|Q(created_by=user)`** (creator
  KEEPS a ticket after handoff; `views.py:576-579` has a STALE comment describing the OLD reversed rule). Admin/Manager (≤20) bypass.
- **`apps/inbox_hub/access.py`** — DEPARTMENT-scoped Hub gate (`hub_rows_q`).
- **`apps/contacts/context.py`** — `build_contact_context` (cache `contact_context_v2`, 60s TTL).

---

## Tickets, Service Layer & SLA

- **`Ticket`** ~58 fields; soft-delete via `is_deleted`/`deleted_at`/`deleted_by` + `SoftDeleteTenantManager`.
  **No `delete()` override** → `.delete()` is a hard delete. `save()` auto-fills tenant/number/company. `clean()`
  (assignee ∈ tenant) NOT called by `save()`.
- **Service layer** (`tickets/services.py`, 20 public fns; no `create_ticket()` — creation is serializer-driven).
  Every mutation writes both `TicketActivity` (timeline, 27 events) + `ActivityLog` (audit, 34 actions) and
  broadcasts on commit. `ALLOWED_TRANSITIONS` slug-keyed; custom statuses unrestricted.
- 🔴 **bulk_update_tickets "delete" HARD-deletes** with no audit/`deleted_by` (`services.py:1366`), vs single-ticket
  `perform_destroy` soft-delete (`views.py:2364`). 🟠 `bulk_action` targets `Ticket.objects.filter(id__in=…)` (skips
  `get_queryset()` → agent row filter not applied). 🟠 **`change_ticket_status` does NOT validate** → kanban
  `move_card` + bulk change_status bypass `ALLOWED_TRANSITIONS`.
- **Kanban sync** (in `tickets/signals.py`, no `kanban/signals.py`): status→column picks lowest-order `.first()`
  (multi-column-per-status silently relocates a drop); pipeline variant matches column by `name__iexact` (rename
  breaks it). `remove_kanban_cards_on_ticket_delete` is post_delete-only → soft-deleted tickets keep CardPosition
  rows; detail serializer hides them, but summary `card_count` is inflated by ghost cards.
- `fire_ticket_assigned_signal` needs `"assignee" in update_fields` (out-of-band full `.save()` sends no notif).
  `fire_webhooks` uses `.delay()` not `on_commit` (`webhook_service.py:142`) → rolled-back txn still fires.
  `escalate_ticket` count has no `F()` (lost update).
- **SLA** (`sla.py`): `get_effective_elapsed_minutes` DOES honour `business_hours_only` and is live. 🟠
  `initialize_sla` seeds with `add_business_minutes` unconditionally → clock mismatch for `business_hours_only=False`
  policies. 24/7 wall-clock fallback when no `BusinessHours`. **Hub SLA is separate + wall-clock only.**
- **Outbound `send_ticket_email`** sends **synchronously** and writes the `out:` threading row only after success.

## Inbox Hub + Email Pipeline

**The seam** (`inbound_email/services.py:355-366`): existing-ticket reply → `_add_reply_to_ticket` (always);
else `TenantSettings.inbox_hub_enabled` (default False) → `park_email_in_hub`; else `_create_ticket_from_email`.
The local `settings = getattr(tenant,"settings")` is a readability-only name-shadow (no module-level django settings
import). Confirmation email sent only if `auto_send_ticket_created_email` explicitly True (default False).

- **HubEmail**: 9 states, 4 priorities (no medium), `ACTIVE_SLA_STATES`=5 (lockstep with partial index). State
  machine: convert/dismiss/reassign/`assign_to` lock via `select_for_update`; **transition + escalate read unlocked**
  → residual lost-update/state-stomp (SQLite makes `select_for_update` a no-op anyway). escalate is a no-op unless
  a genuine transition into ESCALATED.
- **`first_responded_at`** stamped by convert / transition-into-RESPONSE_STATE / claim-or-self-assign (responder
  acting, `actor.pk == new_user.pk`); NOT by manager-routing / engine `assign_to` / dismiss.
- 🔴 `reassign_hub_email` service has no membership check (only the view's `_require_member`); `AssignSerializer.assignee_id`
  queryset is global `User.objects.all()`.
- **AssignmentEngine**: no-dept candidate fallback uses raw role==30; `assign_to` stamps InboundEmail handoff
  (assignee/`inbox_status=PENDING`/`is_read=False`), not `first_responded_at`. **Access is DEPARTMENT-scoped**.
  `check_hub_sla_breaches` (Beat 120s) flags + auto-escalates + one-shot warning.
- **Legacy auto-assign** `auto_assign_ticket`/`get_available_agent` are DEAD; live path is
  `pick_email_agent`/`auto_assign_email_ticket` (gated `auto_assign_inbound_email_tickets`, default False; raw role
  ==30; gates on `!=OFFLINE` not `is_assignable`).
- **Cockpit** (`inbox-hub.js` `?v=16`, labelled "Emails" at `/emails/`): triage over convert/assign/dismiss, 4
  lenses (no "mine"), assignee chip, mobile chip-bar + one-pane swap. Backend `claim/escalate/transition/note`
  exist but the cockpit never calls them (dead-but-present). No `reply` action.
- **Inbound attachments**: authed `attachment/` action, inline raster only (SVG excluded), else forced download +
  `nosniff`. IMAP dedup per (tenant, message_id); 🐛 dedups on `tenant=None` when unresolvable.

## Other Apps — key facts & footguns
- **CRM/Reminders:** Reminder has MEDIUM priority + `due_notified_at` watermark. `fire_due_reminders` (Beat 30s)
  claim-first. `check_overdue_reminders` DEAD (not in Beat). 🔴 `bulk_action` IDOR (H3).
- **Contacts:** `last_activity_at` via `.update()` → no signal (the `signals.py` docstring is WRONG). `ContactEvent`
  not broadcast.
- **Knowledge:** `kb_search` Postgres-only, RAISES on SQLite (doc claims icontains fallback). `vote` handler reads
  `{helpful}` but schema advertises `{value}`; keyed per-session not per-user. `KBRevision`/`KBTicketLink`
  dead-everything. `preview_file` weak DOCX regex-sanitization (XSS risk). 🔴 KB files unauth cross-tenant (C2).
- **Newsfeed:** 24h auto-expiry; create/update/delete Admin/Manager only; drafts hidden. 🐛 `react`/`mark_read`
  bypass the draft filter via known UUID.
- **Notes:** strictly per-user (clean). **custom_fields:** orphan `CustomFieldValue` only on full-clear.
- **comments:** internal bodies redacted on the LiveBus wire. 🔴 cross-tenant read+write (C1).
- **messaging:** DIRECT/GROUP scoped; 🔴 TICKET branch injects cross-tenant participants (H1).
- **analytics:** 🔴 `DashboardView` `IsAuthenticated`-only (M1). Export route is `exports` (button POSTs
  `export-jobs` → 404). XLSX→CSV fallback latent (openpyxl installed).
- **billing:** deprecated `stripe.error.*` (works under pin `<12`). Webhook replay guard correct. `require_feature`
  DEAD. Stripe webhook is a plain Django view (not DRF).
- **attachments:** object-level authz sound; tenant-UUID media gated; hole is `PUBLIC_PREFIXES` (C2).
- **api_keys:** SHA-512, fail-open only on absent header, cross-tenant guard; `prefix` not unique → 500 on collision.
- **VoIP:** structurally non-functional by default (`run_ari_listener` not in PM2; `crm_voip` unconsumed →
  `cleanup_stale_calls` never runs). 🔴 `CallEventConsumer` cross-tenant (H2). `Plan.has_call_recording` zero readers.

## Live Broadcast Layer & WebSockets
- **`broadcast_live_event(tenant, event, payload)`** → group `live_tenant_{pk}`, wire `{type:"live_event", event, payload, ts}`;
  defers via `on_commit` unless `immediate=True`. Client `window.LiveBus` (pub/sub + BroadcastChannel cross-tab);
  `window.LiveConnection` (single `ws/live/`, 25s heartbeat, infinite backoff). **43 signal receivers** across 10
  apps (inbox_hub has NO signals.py — fans imperatively). Tickets do NOT server-broadcast to `live_tenant_*`
  (client-side bridge in `ticket-feed.js`).
- **6 WS consumers** (`main/asgi.py`): ChatConsumer (`ws/messaging/{id}/`, 4001/2/3/4), NotificationConsumer
  (`ws/notifications/`, bare close, no membership check), TicketPresenceConsumer, TicketListConsumer,
  CallEventConsumer (`ws/voip/events/`, bare close, tenant-wide, no membership check — H2), LiveEventConsumer
  (`ws/live/`, DOES membership-check).

## Celery / Beat / PM2
- **8 route globs**; `crm_voip` **unconsumed** by every worker; `crm_webhooks` consumed but producerless
  (`apps/billing/tasks.py` doesn't exist). **12 Beat entries**; DEAD (not in Beat): `check_sla_breach_warnings`,
  `check_overdue_reminders`. UTC-vs-KL skew: `kb-stale-alert` (08:00 UTC → 16:00 local), `kb-gap-digest` (Mon 09:00
  UTC → 17:00 local). Built-in shelve scheduler (django-celery-beat removed for Django 6).
- **PM2:** prod 5 procs (django/worker/beat/flower/smtp), dev 4 (no smtp). `C_FORCE_ROOT=true` on worker+beat+flower.
  Flower default `admin:changeme`. Dev worker watch misses consumers/signals/models/views. Makefile: `logs-django`
  errors (no rule body); `stop`/`restart` skip `crm-smtp`.

## Settings / Env
- `settings/__init__.py`: `DJANGO_DEBUG` default False → prod.py; True → dev.py. `main/checks.py::crm.E001`
  hard-fails a `--deploy` check if DEBUG is True (but runtime never forces it). base.py reads ~39-40 env keys
  (.env.example documents 16). `CRM_FLOWER_AUTH` NOT read by base.py (only PM2 configs).
- 🔴 **The on-disk `.env`** sets `DJANGO_DEBUG=True` + `EMAIL_BACKEND=smtp` + real Gmail creds + Flower
  `admin:changeme` (C3) → dev mail is sent live via `smtp.gmail.com` (NOT captured to `tmp/emails/`); shipping this
  `.env` to prod boots dev.py.
- DRF auth order **JWT → APIKey → Session**; SimpleJWT access 15min/refresh 7d (rotate+blacklist, HS256). Sessions
  `cached_db`, host-only cookies; cross-host auth via `/auth/handoff/`. 14 middleware (4 custom: SessionVersion,
  Tenant, Subscription, RateLimitHeaders). `SubscriptionMiddleware` → 402.

## Frontend

### ⚠ The 4 uncommitted (dirty) files — a NEW visual pass (only working-tree change; details in 02-SUBSYSTEM-REFERENCE)
1. **`base.html`** (2 lines): `custom-v15.css` gains **`?v=16`** (first-ever CSS cache-bust).
2. **`custom-v15.css`** (126 lines): dashboard/analytics gradient-icon restyle; **`kz-fade-up` fill-mode `both →
   backwards`** (clears a residual `transform` that trapped anchored overlays — "Do NOT revert to both"); `.btn-primary`
   gradient+lift.
3. **`dashboard.html`** (36 lines): 4 stat cards get `.fd-summary-body` + `.fd-summary-icon` (DOM half of the CSS restyle).
4. **`landing_crm.html`** (1405 lines; 1396→1199): full marketing rewrite — hero CTA `/signup/` → `/register/` (fixes
   dead link), Pricing block deleted, static images → inline HTML/CSS mockups. **14 dead footer links remain** + 3 bare socials.

### Stable facts
- **14 JS modules / 6006 LOC** (vanilla): inbox-hub 1513 (`?v=16` — only cache-busted JS), app 1288 (`ReminderAlerts`
  + `ReminderScheduler` + `window.Toast`), voip-softphone 710, custom-select 371, command-palette 337 (⚠ "New Contact"
  → dead `/contacts/new/`), keyboard-shortcuts 318, ticket-feed 248, agent-availability 244, notes-panel 238,
  live-connection 206, rich-editor 191, live-bus 175, api 90, theme 77. **4 divergent WS reconnect loops** (10 / ∞ / 10 / fixed-5s).
- **base.html** (318): mobile detection via `window.innerWidth` (<992/<576) on resize/orientationchange (no touch
  heuristic); body-level `{% block overlays %}` (offcanvas-backdrop stacking fix); `#reminderDueModal` app.js-driven;
  palette `<style>` in `:root`; default theme dark.
- **CSS** (`custom-v15.css`, "Design System v9.0 Crimson Black", only loaded project CSS): `--crm-space-*` (13) and
  `--crm-leading-*` (4) are **DEAD (0 uses)**; `--crm-radius-md` (6px) < `-sm` (8px) quirk. Theme-check via
  `scripts/check_theme.py` (baseline 145/10; live 126). No hex literals in rule bodies.
- **Single-red avatars:** 8 templates unified to `var(--crm-avatar-bg)`; **`emails/list.html` + `agents/list.html`
  still hash a 10-slot palette** (split-brain identity color).
- **Emails/Inbox rename** (intentional name↔label swap): `/emails/`=view `inbox_hub_page` (cockpit),
  `/inbox/`=view `emails_page` (personal); sidebar badge ids swapped vs labels. `kb_sidebar_widget.html` orphan.
- **No custom 404/500/402 templates** (only `403.html`). Landing footer has 14 dead links.

## Testing / CI
- **76 test modules** (69 root + 7 app-level). conftest 343 LOC (16 factories, 20 fixtures, 3 autouse). pytest.ini = 2
  directives, no `asyncio_mode`. `make test-fast` broken (`--timeout=30`, `pytest-timeout` absent from dev.txt).
- **Suite GREEN**: 1078 passed / 22 skipped / 1 xfailed. CI (`.github/workflows/ci.yml`): PG16+Redis7,
  `DJANGO_DEBUG=True`; ruff (non-blocking) → migrate-check → theme-check → pytest (all blocking).

## Management Commands (8)
`provision_tenant` · `seed_plans` · `setup_queues` · `setup_ticket_statuses` · `backfill_sla_audit` ·
`seed_inbox_hub_defaults` · `run_smtp_server` · `run_ari_listener` (NOT in any PM2 config).
⚠ A fresh tenant seeds **0 queues/statuses/categories** (post_save seeds only settings + roles) → first
"Create Ticket" needs `setup_queues` + `setup_ticket_statuses` first.

## Docs
- `CLAUDE.md` (this file) — day-to-day source of truth for the **on-disk** state.
- **`docs/audits/2026-07-27-deep-dive/`** — this pass's detailed backing store (overview · security findings · subsystem reference).
- `docs/architecture.md` (v1.0, STALE) · `docs/reference/*-surface.md` (2026-05-22, STALE) · `docs/qa-audit-2026-06-14/`
  (11 files, drove Sprint 0) · `docs/testing/` (manual checklist + 2026-06-29 QA run) · `docs/deploy/protected-media.md`.
  CLAUDE.md wins on any disagreement. (Origin-only doc folders like `docs/history/`, `docs/qa-audit-2026-07-13/` do
  NOT exist in this checkout.)

## Pitfalls (quick list)
1. **This checkout is `df9b29d`, 5 commits behind origin & PRE-hardening** — the security bugs the memory calls
   "fixed" are PRESENT. Verify behavior against SOURCE, never the live DB (it was migrated by newer code).
2. **JWT `tenant_id` is never enforced** → any JWT works on any subdomain; bare-`IsAuthenticated` viewsets are
   cross-tenant-open (C1/H1/H2/H4/M1-M4). `HasTenantPermission`/`IsTenantMember` are SOUND — don't "fix" them.
3. Zero-perm RBAC seeding (C4) → fresh tenants run on the coarse hierarchy floor; an Agent can create/update
   anything at ≤30 regardless of codename.
4. bulk_update_tickets HARD-deletes; change_ticket_status doesn't validate; reminder bulk_action IDOR.
5. `.env` on disk = DEBUG + live Gmail + Flower admin:changeme.
6. VoIP non-functional by default; `crm_voip`/`crm_webhooks` queue issues; UTC beat skew.
7. Always `effective_role` (raw-role drift at 10 sites). Always `tenant_context` off-request.
8. `kb_search` raises on SQLite; `make test-fast`/`make logs-django` broken; `stop`/`restart` skip smtp.
