# Kanzen — Project Intelligence

> Last refreshed: **2026-06-18** — independent re-verification pass (CLAUDE.md + MEMORY.md **detached**; 6 parallel agents each re-derived the load-bearing facts FRESH from code with no access to these docs; full `pytest` run + every quality gate re-executed). **Finding: the working tree is byte-stable since the 2026-06-16 refresh** — `find apps main static templates tests -newermt "2026-06-16 06:00"` returns nothing, so every count and claim below was re-confirmed (zero contradictions across all 6 agents) rather than re-derived from changed code. Previous deep-dive: 2026-06-16. Verified against branch **`qa/sprint-0-critical-fixes`** (forked from `main` @ HEAD **`9575577`**; the branch tip is still that commit — **all work below is UNCOMMITTED working tree**).
>
> **⚠️ The working tree is DIRTY — THREE stacked, uncommitted layers** (43 modified tracked files + 7 untracked code/test files + the QA-audit docs dir + a new `.github/`). A reviewer would split:
> - **Layer 1 — QA Sprint 0 critical-fixes** (the reason this branch exists; implements **all 8 launch-blockers** from `docs/qa-audit-2026-06-14/06-REMEDIATION-PLAN.md` — see **§Sprint 0 Hardening**): `main/settings/__init__.py` (DEBUG default flip), `apps/messaging/{mentions,consumers,serializers,views}.py` (cross-tenant user scoping), `apps/attachments/{access.py [NEW],views.py}` (object-level authz + authed `download/`), `apps/billing/{management/commands/seed_plans.py,webhooks.py}` + mig `billing/0003` (VoIP seeding + re-subscribe), `apps/inbound_email/imap_poller.py` (per-tenant dedup), `.github/workflows/ci.yml` (**NEW — first-ever CI**), + re-greened stale tests + 3 new test modules.
> - **Layer 2 — Feature A "Reminder-due popup"**: `apps/crm/{models,tasks}.py`, `apps/notifications/{models,services}.py`, `main/settings/base.py`, `static/js/app.js`, `templates/{base.html,pages/reminders/list.html}`, `static/css/custom-v15.css`, `tests/test_recalls.py` + migs `crm/0005`, `notifications/0006`.
> - **Layer 3 — Feature B "Create ticket from email with overrides" + inbound-email attachments + Inbox-Hub access refactor** (these three landed together as Hub-cockpit work and now overlap): `apps/inbound_email/{api_views,services,serializers}.py` + `apps/inbound_email/ticket_overrides.py` **[NEW]** + `apps/inbound_email/attachments.py` **[NEW]**, `apps/inbox_hub/{services,serializers,views,permissions,access}.py`, `apps/{nav/views,tenants/context_processors,tenants/frontend_views}.py`, `templates/pages/{emails,inbox_hub}/list.html`, `static/js/inbox-hub.js` (`?v=10`), `tests/test_inbox_hub.py` + `tests/test_inbound_email.py`.
>
> **Quality gates (re-run 2026-06-18 — identical to 2026-06-16):** full `pytest -q` = **894 passed / 0 failed / 22 skipped / 1 xfailed** ✅ **GREEN** (182s on SQLite; unchanged from the 06-16 run). `makemigrations --check --dry-run` = **"No changes detected" (exit 0)**. `python scripts/check_theme.py` **PASSES** ("146 pre-existing hex literals tracked", baseline 147/11). `ruff check .` = **197 errors** (157 auto-fixable; mostly F401 unused-import + F841 + F541 — non-blocking). **CI runs against PostgreSQL 16 + Redis 7** on every push-to-`main` + PR (ruff `continue-on-error`; migrate-check / theme-check / pytest are blocking).
>
> **⚠️ Two residual gaps the Sprint 0 fixes did NOT fully close:** (1) the DEBUG fix is the **default-flip only** — there is NO startup assertion that `DEBUG is False` under https, and no dedicated settings-default unit test (CI just sets `DJANGO_DEBUG="True"` explicitly). (2) Attachment authz gates the **DRF API** (retrieve/download/upload/destroy + the new inbound-email `attachment/` stream) but **raw `/media/` is still served unauthenticated** — the in-code `download` docstring itself flags X-Accel-Redirect as future work.
>
> **⚠️ What changed since the 2026-06-15 snapshot (same branch, same tip — work landed across 2026-06-15→16):** three things evolved past the prior doc. (1) **Inbox Hub access was re-architected AGAIN** — the binary `UserGroup` gate is **GONE** (`user_in_any_group` fully deleted) and replaced by **DEPARTMENT-SCOPED visibility** (`apps/inbox_hub/access.py`: `can_access_inbox_hub` + `hub_rows_q` + `agent_can_see_hub_email` + `user_department_ids`). (2) **Feature B's Hub-cockpit reachability gap is CLOSED** — a shared validator `apps/inbound_email/ticket_overrides.py::build_ticket_overrides` now serves BOTH the Emails-page `create_ticket` AND the Hub `convert_to_ticket`, so the full 9-field override set reaches both; `ConvertToTicketSerializer` is now schema-only; the cockpit gained a full `#ihConvertPanel` offcanvas (TipTap description + tags + flatpickr due-date). (3) **NEW inbound-email attachments feature** — `apps/inbound_email/attachments.py` (`serialize_attachments` + `stream_attachment`) + an authed `attachment/` `@action` on BOTH `InboundEmailViewSet` and `HubEmailViewSet`. Counts that moved 06-15→16: **pytest 864 → 894**; **css 24,996 → 25,149 LOC**; **JS 5,416 → 5,722 LOC** (`inbox-hub.js` 1,177 → **1,378** `?v=8 → ?v=10`, `app.js` 1,034 → **1,139**); **2 new untracked code modules** (`ticket_overrides.py`, `attachments.py`). Migrations still **119**, models still **91**.
>
> **Still-open (NOT addressed by Sprint 0 — pre-existing footguns, carried forward):** `role` vs `effective_role` drift (~20 sites: `tickets/views.py` list, `analytics/services.py`, `kanban/serializers.py`, `agents/services.py::pick_email_agent`, Hub `_candidate_user_ids`); model `clean()` validators never called by `save()` (`Ticket` assignee, `Account.health_score`, `Contact.lead_score`); internal-comment bodies broadcast tenant-wide on the LiveBus; Inbox-Hub `first_responded_at` never written → SLA response-breach always fires + auto-escalates; `check_overdue_reminders` still DEAD (not in Beat); Company custom-fields never synced; `KBRevision`/`KBTicketLink`/`require_feature`/`PARKED_IN_HUB` dead; `DashboardView` under-permissioned; `kanzan_voip` queue unconsumed + `run_ari_listener` not in PM2; logs unrotated (~95MB); command-palette `/contacts/new/` dead link; XLSX-without-openpyxl writes CSV into a `.xlsx`. These are the QA audit's **Sprint 1–3** backlog (`docs/qa-audit-2026-06-14/06-REMEDIATION-PLAN.md`).
>
> **Re-verified factual counts (re-confirmed 2026-06-18, all unchanged since 2026-06-16):**
> - **91 Django model classes** across 21 apps with `models.py` (per-app: tickets 22, inbox_hub 8, accounts 8, knowledge 6, contacts 5, voip 5, analytics 4, billing 4, comments 4, agents 2, custom_fields 2, crm 2, inbound_email 3, kanban 3, messaging 3, newsfeed 3, notifications 2, tenants 2, api_keys 1, attachments 1, notes 1). (Raw `^class`/nested-class greps over-count by including `TextChoices`/`Manager`/`QuerySet`.)
> - **119 migrations** (was 118; **+`billing/0003_backfill_voip_plan_flags`**). `makemigrations --check` clean. `main` has no `migrations/` dir. Latest per heavy app: accounts 0012, agents 0007, inbound_email 0010, tenants 0010, tickets 0027, comments 0010, **billing 0003 (untracked)**, **crm 0005 (untracked)**, **notifications 0006 (untracked)**, inbox_hub 0001. (3 untracked migration files in total.)
> - **INSTALLED_APPS = 46** = 21 `apps.*` + `main` + **24 third-party** (incl. `daphne`, `jazzmin`, 7× `django.contrib.*`, allauth ×6, etc.). **`apps.nav` is NOT installed** — it is the lone URL-only module (no `models.py`/`apps.py`), mounted at `/api/v1/nav/`. There are 22 app dirs; 21 have `models.py`.
> - **MIDDLEWARE = 14 layers.**
> - **28 `path()` in `main/urls.py`**; **23 `/api/v1/*` includes** (22 unique URLConfs — `inbound-email/` dual-mounts as `emails/`). **35 frontend URL paths** in `apps/tenants/frontend_urls.py`.
> - **6 WebSocket consumers** (5 Channels `routing.py` files). `apps/inbox_hub/routing.py` is the **RoutingEngine** (email→department), NOT a Channels route.
> - **27 Celery `@shared_task`** across **10** task modules; **12** in Beat.
> - **42 signal receivers** across 10 apps with `signals.py` + `notifications/signal_handlers.py`. **`apps/inbox_hub/` has NO `signals.py`** and `apps.py` has no `ready()` — it fans events imperatively.
> - **66 test modules** (59 root + 7 app-level — incl. 3 untracked Sprint-0 tests: `test_attachment_authz`, `test_billing_voip_and_webhook`, `test_messaging_tenant_isolation`; the Feature-B/attachments/Hub-access tests were **added into existing** `test_inbox_hub.py` + `test_inbound_email.py`, not new files). conftest 343 LOC = 16 factories + 20 fixtures (3 autouse).
> - **`static/css/custom-v15.css` = 25,149 LOC** (the only loaded project CSS; grew +153 from the Hub-convert offcanvas + override-form styling); `custom.css` = 20,431 LOC (committed snapshot, NOT loaded, theme-check-allowlisted).
> - **48 `.html` templates**; **18 `pages/` subfolders**; **8** `pages/` root files; **6 `includes/`** (1 orphan: `kb_sidebar_widget.html` @158 LOC; `page_back_button.html` included by **18**).
> - **14 JS files / 5,722 LOC** in `static/js/` (`inbox-hub.js` **1,378** `?v=10`, `app.js` **1,139**). **Only `inbox-hub.js` is cache-busted**; the other 13 are unversioned (so an uncommitted `app.js` change can serve stale without a hard refresh).
> - **8 management commands.** **33 Makefile targets** (`logs-django` is in `.PHONY` but has no rule body → calling it errors).
> - **`logs/` is ~95MB** (gitignored, no rotation). `tmp/emails/` holds dev captures. `env` is a committed symlink → `.venv`.

## Project Overview

Multi-tenant CRM, Ticketing, Knowledge Base and VoIP SaaS. **Django 6.0.2 + DRF 3.16+ + Channels 4.2+ + Celery 5.4+** with Bootstrap 5.3.3 + vanilla JS frontend (SIP.js softphone, TipTap rich editor, DOMPurify sanitization). Row-level multi-tenancy via subdomain routing and **contextvars-based** tenant binding (async-safe). Admin is jazzmin-skinned, superuser-locked. PM2 process management. **CI exists as of this branch** — `.github/workflows/ci.yml` runs ruff (non-blocking) + migrate-check + theme-check + `pytest` against **PostgreSQL 16 + Redis 7** on every push-to-`main`/PR; locally `make check` (lint → migrate-check → test) is the same gate.

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
├── apps/                          # 22 dirs; 21 in INSTALLED_APPS (nav is URL-only, no models.py)
│   ├── accounts/                  # Users (+is_service_account), 7-role RBAC + temp-role overrides + temp-perms intersection, invitations, profiles, UserGroups (⚠ NO LONGER the Hub gate — see access refactor; still "one user per group"), middleware
│   ├── agents/                    # AgentAvailability (+last_seen presence heartbeat, is_assignable gate) + CustomAgentStatus + presence.py + reap-stale-presence task + load-fairness pick_email_agent
│   ├── analytics/                 # Reports, dashboard widgets (⚠ DashboardView under-permissioned), exports (PDF/XLSX→CSV placeholder), calendar events
│   ├── api_keys/                  # APIKey (SHA-512) + auth class + viewset + per-key throttle + rate-limit-headers middleware + drf-spectacular extension
│   ├── attachments/               # File uploads (polymorphic GenericFK, python-magic MIME, 25MB cap)
│   ├── billing/                   # Stripe billing, plans, subscriptions, webhooks (5 events), decorators (⚠ require_feature dead; NO tasks.py)
│   ├── comments/                  # Comment + Mention + CommentRead + ActivityLog (34 actions incl. 8 EMAIL_*) + LIVE signals (⚠ broadcasts internal comments)
│   ├── contacts/                  # Contacts, Companies, Accounts, Groups, ContactEvent (360°, NOT live-broadcast) + LIVE signals + context.py (build_contact_context, shared with Hub cockpit)
│   ├── crm/                       # Activity + Reminder (M2M contacts/tickets) + lead/account scoring + LIVE signals + NEW fire_due_reminders task (Feature A)
│   ├── custom_fields/             # EAV custom fields per tenant + sync signals (Ticket + Contact ONLY — NOT Company)
│   ├── inbound_email/             # SMTP+IMAP ingestion; forks on TenantSettings.inbox_hub_enabled → legacy ticket-create OR park in Inbox Hub; agent email-inbox handoff; create_ticket+overrides (Feature B); ticket_overrides.py [NEW shared validator] + attachments.py [NEW: serialize/stream customer-sent files]
│   ├── inbox_hub/                 # Email-triage workspace: 8 models + services + RoutingEngine + AssignmentEngine + state machine + SLA task + 11 viewset actions (+context +attachment) + 4 config viewsets + access.py (⚠ DEPARTMENT-scoped — group gate REMOVED). NO signals.py.
│   ├── kanban/                    # Visual boards, columns (is_personal), polymorphic CardPosition; cross-status drags route through tickets service (full audit/feed/SLA)
│   ├── knowledge/                 # KB articles (PG FTS), categories, search, stale alerts, gap digest, allowed_groups M2M (⚠ KBRevision/KBTicketLink dead-write)
│   ├── messaging/                 # Real-time conversations (WS); Conversation.source_group; attachments on messages (POST broadcast action)
│   ├── nav/                       # URL-only module (BadgeCountView — 7 categories; effective_role; NOT an installed app)
│   ├── newsfeed/                  # Internal announcements, reactions, read receipts + LIVE signals
│   ├── notes/                     # Personal sticky notes (6 colors, pinning) — no signals
│   ├── notifications/             # In-app + email + WebSocket notifications (21 NotificationType incl. 5 HUB_EMAIL_* + NEW REMINDER_DUE); NOT polymorphic (data JSONField)
│   ├── tenants/                   # Tenant model, middleware, frontend views, frontend_urls (35 paths), live broadcast layer, palette; LiveEventConsumer stamps presence on heartbeat
│   ├── tickets/                   # Core ticketing; Queue gains optional department FK; access.py (shared agent-visibility helper); SLA + business hours, CSAT, pipelines, macros, webhooks, deals
│   └── voip/                      # Asterisk ARI integration, SIP softphone, call logs, recordings, queues (runtime is manual-launch — see Pitfalls)
├── main/                          # Django project root
│   ├── settings/{__init__,base,dev,prod}.py  # __init__ branches on DJANGO_DEBUG (**default now False — Sprint-0 fail-safe**, matches base.py) → dev.py / prod.py; base.py holds CELERY_BEAT_SCHEDULE (12) + AGENT_PRESENCE_* + HUB_SLA_*
│   ├── admin.py                   # SuperuserOnlyAdminSite (reassigns admin.site.__class__) + TenantFilteredAdmin mixin (full add/change/save with tenant picker); registers 0 models
│   ├── celery.py                  # Celery app + queue routing (8 globs incl. default) — NO beat schedule (lives in base.py)
│   ├── asgi.py                    # ProtocolTypeRouter: HTTP + WebSocket (6 consumer endpoints, WebSocketTenantMiddleware)
│   ├── context.py                 # contextvars-based tenant context (async-safe)
│   ├── models.py / managers.py    # TimestampedModel, TenantScopedModel; TenantQuerySet, TenantAwareManager (fail-closed .none()), SoftDeleteTenantManager
│   └── urls.py                    # 28 path() (23 /api/v1/ includes; 22 unique URLConfs) + /admin/ + /api/{schema,docs}/ + /accounts/ + frontend ""
├── templates/                     # 48 .html files (18 subfolders under pages/)
│   ├── base.html                  # 292 lines — palette <style>, toast container (hardcoded z-index:1090), live-bus + live-connection JS, Flatpickr loader, sidebar-collapse FOUC fix, NEW #reminderDueModal
│   ├── includes/                  # 6 files — navbar, sidebar (Inbox Hub entry gated {% if can_access_inbox_hub %}), softphone, messages, page_back_button (18 includes), kb_sidebar_widget (ORPHAN)
│   ├── pages/                     # 18 subfolders + 8 root html files (403, api_quickstart, calendar, dashboard, landing, login, profile, register)
│   ├── landing/landing_crm.html   # Standalone marketing page (1,393 LOC; doesn't extend base.html)
│   └── {auth,knowledge,notifications,tickets}/email/  # 6 transactional email templates
├── static/
│   ├── css/custom-v15.css         # 25,149 LOC (loaded; "Crimson Black v9.0")
│   ├── css/custom.css             # 20,431 LOC (committed snapshot — NOT loaded; allowlisted in theme check)
│   ├── images/                    # Logo, favicon (DP.png), hero artwork
│   └── js/                        # 14 vanilla-JS modules (5,722 LOC, incl. live-bus + live-connection + inbox-hub.js 1,378 + app.js 1,139)
├── tests/                         # 59 root pytest modules + 7 app-level (66 total; +3 untracked Sprint-0)
├── conftest.py / pytest.ini       # 343 LOC: 16 factories + 20 fixtures (3 autouse); pytest.ini = 3 lines, no asyncio_mode
├── requirements/{base,dev,prod}.txt   # prod = -r base.txt (no extras); base ~30 lines; dev = base + 11 tools (now incl. pytest-asyncio; ⚠ still no pytest-timeout)
├── .github/workflows/ci.yml       # NEW (Sprint 0) — first CI: ruff(non-blk)+migrate-check+theme-check+pytest on PG16+Redis7
├── requirements.txt               # ROOT — byte-identical duplicate of requirements/base.txt
├── ecosystem.config.js            # PM2 prod: 5 processes
├── ecosystem.dev.config.js        # PM2 dev: 4 processes (no SMTP, watch-mode reloads)
├── Makefile                       # 33 targets (logs-django in .PHONY but no rule body — calling it errors)
├── docs/                          # README + architecture.md (v1.0 2026-02-06 STALE) + reference/{4 docs, STALE pre-Inbox-Hub} + ui-consistency-audit.md (stale) + **qa-audit-2026-06-14/ (11 files — the audit driving this branch)**
├── tmp/emails/                    # Dev email capture (filebased EmailBackend, gitignored — 20 captures)
├── logs/                          # PM2 log files — ~95MB (gitignored; no rotation)
├── media/                         # User-uploaded: tenants/{id}/… and inbound_emails/{id}/…
├── scripts/                       # check_theme.py + .theme_baseline.json (147 hex literals across 11 files)
├── db.sqlite3                     # Dev database (~12MB, gitignored)
├── celerybeat-schedule            # Celery Beat shelve file (built-in scheduler — django-celery-beat removed for Django 6 compat)
└── .env                           # ~26 keys (.env.example covers 16; ~22 read-but-undocumented vars)
```

## Sprint 0 Hardening (UNCOMMITTED — the reason branch `qa/sprint-0-critical-fixes` exists)

A 2026-06-14 end-to-end QA/security audit (`docs/qa-audit-2026-06-14/`, readiness **38/100**) found **6 confirmed Criticals + no CI**. This branch implements all 8 Sprint-0 launch-blockers from `06-REMEDIATION-PLAN.md`. **All 8 verified present 2026-06-15; suite GREEN.** Per-fix detail (the inline "⚠ broken" notes elsewhere in this doc are superseded here):

1. **DEBUG split-default → fixed (default flip).** `main/settings/__init__.py:12` flipped `env.bool("DJANGO_DEBUG", default=True)` → **`default=False`**, so an UNSET `DJANGO_DEBUG` now fails safe to `prod.py` (`DEBUG=False`, real `ALLOWED_HOSTS`) instead of silently loading `dev.py`. ⚠ **Caveat:** default-flip ONLY — no runtime "DEBUG must be False under https" assertion, no dedicated settings-default test; CI sets `DJANGO_DEBUG="True"` so tests still run under dev.

2. **Messaging cross-tenant enumeration/injection → fixed (4 sites).** User resolution is now membership-scoped: `mentions.py:73` (`notify_mentions`), `consumers.py:317` (`ChatConsumer._create_message`), `views.py:398` (`MessageViewSet`) all filter `memberships__tenant=tenant, memberships__is_active=True`. `serializers.py::ConversationCreateSerializer.validate` adds a `TenantMembership.exists()` check for DM creation (`"User is not a member of this tenant."`) and validates every supplied `user_ids` member for manual-group creation (`"One or more users are not members of this tenant."`). Read-side (mentions, no notification leak) **and** write-side (DM/group) closed. Tests: `tests/test_messaging_tenant_isolation.py` (5).

3. **Attachment authz → fixed (API-level).** New `apps/attachments/access.py::can_access_target(user, tenant, content_type, object_id)` — single source of truth. Admin/Manager (`effective_role` ≤20) bypass; non-members → sentinel level 999. Per-type: **tickets.ticket** → `tickets.access.agent_can_see_ticket`; **comments.comment** → internal+level>30 denied, else if parent is a Ticket delegate to `agent_can_see_ticket`; **messaging.message** → must be a `ConversationParticipant`; **all other tenant-scoped targets fall through to allow** (tenant isolation still applies). `views.py`: new `CanAccessAttachmentObject(BasePermission)` (object-level on retrieve/destroy/download), a new authed **`@action download/`** (streams via `FileResponse(as_attachment=True)`), and an explicit `can_access_target` check in `create()` (upload has no object yet → can't use `has_object_permission`). ⚠ **Caveat:** raw `/media/` URLs remain **unauthenticated** — only the DRF endpoints are gated (the `download` docstring itself flags X-Accel-Redirect/X-Sendfile as the prod follow-up). Tests: `tests/test_attachment_authz.py`.

4. **Billing VoIP entitlement → fixed (seeder + backfill).** `seed_plans.py` now sets per plan: Free `has_voip=False / has_call_recording=False / max_calls_per_month=0`; **Pro** `True / True / 1000`; **Enterprise** `True / True / None` (unlimited). New data migration **`billing/0003_backfill_voip_plan_flags`** backfills existing `Plan` rows by tier via `.update()` (forward-only, noop reverse). `check_call_limit` now permits calls on Pro/Enterprise. **This invalidates the old "VoIP denies everyone" pitfall.**

5. **Billing re-subscribe IntegrityError → fixed (repoint OneToOne).** `webhooks.py::_sync_subscription_from_stripe:135-159` — when a NEW `stripe_subscription_id` arrives for a tenant that already has a `Subscription` row (re-subscribe after cancel; `Subscription.tenant` is OneToOne), it now **repoints that row** (copies `defaults`, sets the new sub-id, `.save()`, returns early) instead of INSERTing a duplicate → no IntegrityError. Keyed on `tenant`, not on a `.deleted` event. Tests: `tests/test_billing_voip_and_webhook.py`.

6. **IMAP cross-tenant dedup → fixed (per-tenant).** `imap_poller.py::_ingest_one` removed the global `InboundEmail.objects.filter(message_id=…).exists()` and now resolves the tenant first (`resolve_tenant_from_address(recipient)`), dedups on **`filter(tenant=tenant, message_id=…)`**, and stamps `tenant=tenant` at `create()`. Two tenants can receive the same Message-ID. ⚠ Unresolvable recipient → falls back to `tenant=None` scope (still created). Tests: `tests/test_imap_poller_safety.py::TestCrossTenantDedup`.

7. **CI → added.** `.github/workflows/ci.yml`: single `test` job, `ubuntu-latest`, Python 3.12, on push-to-`main` + all PRs (concurrency-cancel). Services **postgres:16** + **redis:7** (health-checked) — exercises PG-only paths (KB FTS, `SELECT … FOR UPDATE`). Steps: ruff (**`continue-on-error: true`** — non-blocking until lint debt cleared), `makemigrations --check` (blocking), `scripts/check_theme.py` (blocking), `pytest -q` (blocking). Inlines `make check` rather than calling it.

8. **Stale tests re-greened.** Outbound-email tests (`test_email_outbound.py`, `test_outbound_email.py`) re-pointed `@example.com` → **`@clientmail.com`** (outbound now skips RFC-2606 reserved domains). `test_badges.py` 14.05/14.09 rewritten comments→**unread-chat** (matching the already-shipped `nav/views.py` badge repurpose). `test_comment_visibility.py` now also clears `assignee` (the tightened agent-visibility rule needs the ticket unassigned, not just self-created).

> **Net:** the 6 audit Criticals + CI gap are closed (with the 2 caveats above). The audit's **Sprint 1–3 backlog** (role/effective_role drift, `clean()` validators, LiveBus internal-comment leak, Hub `first_responded_at`, custom-fields Company sync, dead code, a11y, perf N+1s, log rotation, `kanzan_voip` worker) is **NOT** in this branch.

## Multi-Tenancy Architecture

### Three-Layer Isolation

1. **TenantMiddleware** (`apps/tenants/middleware.py`): Resolves tenant from subdomain (`{slug}.localhost` / `{slug}.{BASE_DOMAIN}`), or `TenantSettings.domain` for custom domains. `_extract_slug` rejects nested sub-subdomains on `{slug}.{BASE_DOMAIN}`. Sets `request.tenant` and binds context. **`EXEMPT_PATH_PREFIXES` (17 entries):** `/static/`, `/media/`, `/api/v1/accounts/auth/`, `/api/v1/billing/plans/`, `/api/v1/billing/webhook/`, `/api/v1/tickets/csat/`, `/api/docs/`, `/api/schema/`, `/accounts/`, `/inbound/email/` (⚠ dead — no URLConf maps to it), `/login/`, `/register/`, `/logout/`, `/verify-email/`, `/verify-email-sent/`, `/setup-company/`, `/workspaces/`. **`/admin/` is NOT exempt** — a dedicated branch resolves the tenant from subdomain when present, sets context **regardless (even to None)**, and defers access control to `SuperuserOnlyAdminSite`. A real-host lookup that resolves to no tenant → `JsonResponse(404)` (JSON even for HTML requests). **`/auth/handoff/` intentionally NOT exempt** — it must resolve the tenant to verify membership.

2. **TenantAwareManager** (`main/managers.py`): Default `objects` manager auto-filters by `get_current_tenant()`. Returns an **empty queryset** (`.none()`) when no tenant in context (fail-closed). Use `Model.unscoped` for cross-tenant queries. `SoftDeleteTenantManager` adds an `is_deleted=False` filter on top.

3. **TenantScopedModel** (`main/models.py`): Abstract base. UUID PK + Timestamped + `tenant` FK (CASCADE, `editable=False`, db_index=True). `objects = TenantAwareManager()`, `unscoped = models.Manager()`. Overridden `save()` auto-assigns `tenant` from context; **raises `ValueError`** if no tenant is bound and none provided.

### Async-Safe Tenant Context (`main/context.py`)

```python
set_current_tenant(tenant); get_current_tenant(); clear_current_tenant()  # clear hard-sets None
with tenant_context(tenant): ...  # snapshots+restores previous (nesting-safe — preferred for tasks/consumers)
```
A single `contextvars.ContextVar("current_tenant", default=None)` — safe across asyncio tasks and Channels consumers. **`clear_current_tenant()` is NOT nesting-safe** (hard-sets None regardless of prior); middleware uses it in `finally` (fine for a per-request lifecycle). `WebSocketTenantMiddleware` resolves tenant from the Host header, sets `scope["tenant"]`, binds context, clears in `finally`.

### Admin: jazzmin theme + Superuser Lock + Tenant-Filtered Mixin (`main/admin.py`)

- **`daphne`/`jazzmin` are INSTALLED_APPS entries 1–2.** Admin is a **jazzmin "darkly"-themed** site (`JAZZMIN_SETTINGS` defines `search_model = ["accounts.User","tenants.Tenant"]` + icons for ~30 models). jazzmin only re-skins templates.
- **`SuperuserOnlyAdminSite`** reassigns `admin.site.__class__` at import time (a global monkeypatch); `has_permission(request)` = `is_active AND is_superuser`. Non-superusers get 403 on `/admin/` regardless of `is_staff`. jazzmin does NOT override `has_permission`, so the lock holds.
- **`TenantFilteredAdmin` mixin** — drop-in for any `ModelAdmin` of a `TenantScopedModel`: `get_queryset` uses `model.unscoped.all()` then filters by `request.tenant`; `get_form` injects a `tenant = forms.ModelChoiceField` (because the model field is `editable=False`) with a guard for Django's recursive `_get_form_for_get_fields` discovery pass; `save_model` backfills `obj.tenant`. **`main/admin.py` registers NO models** — it only exports the mixin and swaps the site class; registration happens in each app's own `admin.py`.

### Palette (`apps/tenants/colors.py::derive_palette`)

`derive_palette(primary, accent)` returns a ~21-key dict of CSS custom-property values (50–900 lightness scale via `colorsys` HLS, hover/active/dark/light/subtle/ring/rgb + WCAG-picked `text_on_primary`/`text_on_accent` = white `#FFFFFF` vs near-black `#0B0B0B`; `logger.warning` if primary contrast < AA 4.5). Defaults `#C1121F`/`#E11D2D` (Crimson Black) on bad/empty hex. Model defaults are `#6366F1`/`#F59E0B`. Wired via `tenants/context_processors.py` → `tenant_palette` → base.html `:root` block.

## Settings Split, Redis, Ports

- `main/settings/__init__.py`: `from .base import *`, then `if env.bool("DJANGO_DEBUG", default=False)` → `from .dev import *` (try/except ImportError) **else** `from .prod import *`. **✅ Split-default footgun FIXED (Sprint 0):** `__init__` now defaults `DJANGO_DEBUG=False`, matching `base.py` — an unset var fails safe to `prod.py`. ⚠ Still no startup assertion that `DEBUG is False` under https; CI sets `DJANGO_DEBUG="True"` explicitly to keep tests on dev.py.
- **Redis:** db3 cache + `cached_db` sessions (`KEY_PREFIX="kanzan"`); db4 Celery broker (result backend = `django-db`, not Redis); db5 Channels (`prefix="kanzan:channels"`).
- **Sessions** are `cached_db`, **host-only cookies** (no `Domain` — Chrome refuses `Domain=.localhost`); cross-host auth uses signed handoff tokens via `/auth/handoff/`.
- DRF auth order **JWT → APIKey → Session**; SimpleJWT access 15min/refresh 7d (rotate+blacklist, HS256, signing key `JWT_SECRET_KEY` → falls back to `SECRET_KEY`). Allauth `ACCOUNT_LOGIN_METHODS={"email"}`; `django.contrib.sites` NOT installed. File upload caps 25MB. `django.contrib.postgres` is installed (for KB FTS).

## Live Broadcast Layer

Unified pub/sub real-time layer: a per-tenant WebSocket fans server-side mutations to a client-side `LiveBus`. Coexists with per-domain consumers (chat, notifications, ticket-feed, presence, voip).

### Backend

- **`apps/tenants/live.py::broadcast_live_event(tenant, event, payload=None, *, immediate=False)`** — `tenant` may be a model instance OR a raw pk. Group: `live_tenant_{pk}`. Wire shape: `{type:"live_event", event:"<domain>.<verb>", payload:{...}, ts:ISO8601}`. Defers via `transaction.on_commit` (unless `immediate=True`); swallows `_send` exceptions (best-effort, logs).
- **`apps/tenants/consumers.py::LiveEventConsumer`** (group `live_tenant_{tenant_id}`). Anonymous → close **4001**; no tenant → close **4001**; non-member → close **4003** (membership verified even for valid JWTs). On `connect` stamps agent presence (`is_connect=True`); on each inbound `{action:"ping"}` re-stamps (`is_connect=False`) and replies `{type:"live.pong"}`. Presence is intentionally NOT cleared on `disconnect` (avoids tab-refresh flapping) — the reaper ages stale sessions out. The `live_event` handler re-shapes to `{type:<event name>, payload, ts}` (the wire `type` is the dotted event name, NOT `"live_event"`).
- **`apps/tenants/routing.py`** → `re_path(r"ws/live/$", LiveEventConsumer.as_asgi())`.

### Signal Emitters (5 apps via signals.py + inbox_hub imperative + presence + the new reminder.due task)

| App / source | Trigger | Verbs |
|-----|-----------|-------|
| `accounts` (`signals.py`) | `TenantMembership.post_save/delete`, `Profile.post_save`, `User.post_save` (fans across every active membership; emits `avatar` URL) | `membership.created/updated/deleted`, `profile.created/updated`, `user.updated` |
| `comments` (`signals.py`) | `Comment.post_save/delete` | `comment.created/updated/deleted` (⚠ **broadcasts `is_internal` bodies too** — clients expected to filter; latent info-leak) |
| `contacts` (`signals.py`) | `Contact/Company/Account/ContactGroup × post_save/delete` (ContactEvent intentionally skipped — and `last_activity_at` is written via `.update()` so it fires **no** signal either) | `contact.*`, `company.*`, `account.*`, `contact_group.*` |
| `crm` (`signals.py`) | `Activity/Reminder × post_save/delete`. Reminder verb resolved by state (no `rescheduled` verb → reschedule emits `updated`) | `activity.*`, `reminder.created/updated/completed/cancelled/deleted` |
| `newsfeed` (`signals.py`) | `NewsPost.post_save/delete`, `NewsPostReaction.post_save/delete` (compact payload, no body) | `newsfeed.created/updated/deleted`, `newsfeed.reacted` (`added: bool` — add AND remove both emit) |
| `inbox_hub` (imperative — service/routing/assignment) | park/route/assign/transition/escalate/reassign/convert/dismiss | `hub_email.created/transitioned/assigned/reassigned/escalated/converted_to_ticket/dismissed` (all 7 emitted) |
| `agents`/`tenants` (presence) | `presence.handle_live_heartbeat`, `reap_stale_presence` task | `agent.presence` (broadcast `immediate=True`) |
| `crm` **task** (Feature A) | `fire_due_reminders` per due reminder | **`reminder.due`** (imperative, tenant-wide, payload incl. `recipient_id`) |

App configs (`apps/{accounts,comments,contacts,crm,newsfeed}/apps.py`) import their `signals` module in `ready()`. **`apps/inbox_hub/apps.py` has NO `ready()`**; no `signals.py` exists. **Tickets do NOT server-side broadcast to `live_tenant_*`** — `tickets/services.py::broadcast_ticket_event` publishes only to `ticket_feed_{tenant_id}`; the LiveBus bridge is **client-side** in `static/js/ticket-feed.js`.

### Frontend

- **`static/js/live-bus.js`** (175 LOC) — global `window.LiveBus`. API: `on/onMany/publish/debounce/rafBatch/isConnected/setChannelState`. Wildcard `"*"` subscriber gets all. Cross-tab fan-out via `BroadcastChannel('kanzan-live')` (`fromTab` flag). Handler errors caught + logged.
- **`static/js/live-connection.js`** (206 LOC) — global `window.LiveConnection`. Single shared `wss?://host/ws/live/`. Skips pre-auth pages and pages without a `sessionid` cookie. Exponential backoff 1s→30s ±20% jitter, **infinite** retries. **25s heartbeat `{action:"ping"}`** with 8s pong timeout — this ping drives server-side presence stamping. ⚠ Code comments saying the server "doesn't speak heartbeat / ignores receive_json" are **stale** — it does stamp presence (harmless; any inbound counts as a pong).
- **Wiring (`templates/base.html`)** — always: Bootstrap → DOMPurify → live-bus.js → api.js → app.js → command-palette.js → custom-select.js; then conditional on `tenant and user.is_authenticated`: live-connection.js → agent-availability.js → notes-panel.js → keyboard-shortcuts.js → ticket-feed.js → (if `voip_enabled`) SIP.js CDN + voip-softphone.js. **`inbox-hub.js` (`?v=10`) and `rich-editor.js` are page-specific** via `{% block extra_js %}`. `theme.js` loads synchronously in `<head>` (FOUC guard). **Only `inbox-hub.js` is versioned** — the other 13 are unversioned (so an uncommitted `app.js` change can serve stale without a hard refresh).

### Channel-Layer Groups & Close Codes

- `live_tenant_{tenant_id}` — live events; `notifications_{user_id}`; `chat_{conversation_id}`; `ticket_feed_{tenant_id}`; `ticket_{ticket_id}_presence`; `voip_{tenant_id}`.
- `1000` clean close; `4001` anon/no-tenant (chat, presence, list, live); `4002` invalid conversation UUID (chat); `4003` non-member (presence/list/live); `4004` conversation tenant ≠ Host (chat). **`NotificationConsumer` and `CallEventConsumer` use a bare `close()` (no code)** for rejections — the asymmetry vs the explicit-code consumers.

## Feature A — Reminder-Due Popup (UNCOMMITTED working-tree feature)

A "can't-miss" alert when a reminder comes due: a centered modal + Web-Audio chime + desktop/OS notification, delivered over the existing `/ws/notifications/` rail so it works on every authenticated page. Supersedes the "reminders never auto-fire" gap **for due alerts only** (overdue escalation still dead).

### Data model + migration
- **`Reminder.due_notified_at`** (`apps/crm/models.py`, `DateTimeField(null=True, blank=True)`) — a **watermark, not a boolean**. Help-text documents the re-arm contract. **Not indexed** (the existing `reminder_overdue_idx` partially covers the hot filter; the `F()` predicate is a sequential filter within an already-narrow set).
- **mig `crm/0005_reminder_due_notified_at`** — `AddField` + `RunPython suppress_existing_due_reminders` (reverse=noop): backfills `due_notified_at = F("scheduled_at")` for reminders **already past-due AND active** at migrate time, so the first 30s tick does NOT pop the entire historical backlog (equal stamp counts as "handled"; future reschedules still re-arm). Correctly uses `Reminder.objects` (NOT `.unscoped` — `apps.get_model` returns a plain historical Manager with no `unscoped`).

### Notification type
- **`NotificationType.REMINDER_DUE`** (`apps/notifications/models.py`) → NotificationType now **21 members** (mig `notifications/0006` AlterFields both `Notification.type` and `NotificationPreference.notification_type`).
- Added to **`INTERNAL_ONLY_TYPES`** frozenset (`notifications/services.py`) → **in-app/WS only, never emailed** (now 5 members: TICKET_OVERDUE, TICKET_FOLLOWUP_OVERDUE, REMINDER_DUE, REMINDER_OVERDUE, AGENT_STATUS_CHANGE). **No new creator fn** — reuses the generic `send_notification(...)`.

### The task — `apps/crm/tasks.py::fire_due_reminders` (Beat `fire-due-reminders` @ 30s → kanzan_default)
- `@shared_task(bind, max_retries=1, acks_late)`. Iterates `Tenant.objects.filter(is_active=True)` (per-tenant + per-row try/except isolation), `Reminder.unscoped`.
- **Fire condition:** `scheduled_at <= now AND completed_at IS NULL AND cancelled_at IS NULL AND (due_notified_at IS NULL OR due_notified_at < F("scheduled_at"))`. The `< scheduled_at` half is the **re-arm guard** — a forward reschedule (or raw PATCH of `scheduled_at`) leaves the old stamp behind → auto-re-arms with no flag reset.
- **Recipient** = `assigned_to or created_by` (`created_by` is PROTECT → always a recipient — distinct from the dead `check_overdue_reminders` which skips unassigned).
- **Claim-first ordering:** stamps `Reminder.unscoped.filter(pk=).update(due_notified_at=now)` **BEFORE** `send_notification`. The task runs outside an atomic block (synchronous WS push), so claim-first means a downstream failure costs at most one *missed* alert, never a duplicate loop. `.update()` (not `.save()`) avoids re-tripping the Reminder `post_save` signal. ⚠ No `select_for_update` → a theoretical SELECT-then-UPDATE race if two workers overlap (single-beat avoids it).
- Emits `send_notification(REMINDER_DUE, data={reminder_id, contact_ids, priority, scheduled_at, url:"/reminders/"})` over `notifications_{recipient_id}` **AND** a separate tenant-wide `broadcast_live_event(tenant, "reminder.due", {...recipient_id...})` (the latter only drives the reminders-list refetch, not the popup).

### Frontend
- **`static/js/app.js::ReminderAlerts`** IIFE (push-driven, NOT a poller): in `initNotifications`, `if (data.type === 'reminder_due') ReminderAlerts.show(data); else showFlyout(data)`. `show()`: arms audio + `Notification.requestPermission` on first gesture; plays a **Web-Audio two-tone chime** (880/1174.66 Hz, no asset); fires a desktop `Notification` (`requireInteraction:true`, `tag:reminder-<id>` OS-dedup, click → `/reminders/`); queues + serializes a centered `#reminderDueModal` (one at a time, drains 250ms after `hidden.bs.modal`); **degrades to a sticky 8s Toast** on pre-auth pages. New `NOTIF_TYPE_CONFIG` rows for `reminder_due` + `reminder_overdue`. ⚠ `reminder_due` does NOT bump the sidebar Reminders badge (`NOTIF_TO_BADGE` maps only `reminder_overdue`).
- **`templates/base.html`** `#reminderDueModal` — `data-bs-backdrop="static"` (must be acknowledged via a button), inside the authed block. `#reminderDueDismiss` id is markup-only (dismiss works via `data-bs-dismiss`).
- **`static/css/custom-v15.css`** `.reminder-due-*` block (+62 LOC, token-only zero hex → theme-check green): pulsing icon (`@keyframes reminderDuePulse`), `prefers-reduced-motion` guard.
- **`templates/pages/reminders/list.html`** (+3 LOC) — adds `'reminder.due'` to the LiveBus refetch subscription (debounced 500ms). ⚠ Does NOT filter by `recipient_id` (harmless — refetch is server-scoped).
- **Tests** (`tests/test_recalls.py::TestFireDueReminders`, 9): fires once, future excluded, completed/cancelled excluded, creator fallback, re-arm on move-forward, suppressed-when-already-notified, internal-only, **claim-first-prevents-refire-on-delivery-failure**.

## Feature B — Create Ticket From Email With Field Overrides (UNCOMMITTED working-tree feature)

Upgrades email→ticket conversion from a one-click POST to a full **override form** so an agent can craft a *proper* ticket (refined subject, written description, priority/queue/status/assignee/category/tags/due_date) instead of accepting the raw email-derived defaults. **The 2026-06-15→16 work CLOSED the old Hub-cockpit reachability gap** — both surfaces now share one validator and carry the full 9-field override set.

- **`apps/inbound_email/ticket_overrides.py::build_ticket_overrides(data, tenant)` [NEW — single source of truth].** Extracted from the old private `api_views._build_ticket_overrides`. Used by **BOTH** email→ticket surfaces so they can never drift. Validates → raises **DRF 400 (not 500)** on bad input: `subject` ≤255, `description` ≤20000 (Ticket.description is an unbounded TextField and `save()` does no `full_clean()`, so it's bounded manually), `priority` ∈ `Ticket.Priority.choices` (case-insensitive), `category` ≤100 (CharField, not a FK), `queue`/`status` tenant-scoped PK lookup (malformed UUID → clean 400), **`status` rejects `is_closed`** (pre_save won't stamp resolved/closed_at on creation), `assignee` must be an active `TenantMembership`, `due_date` parsed + made-aware (non-string → 400), `tags` list each ≤50. Blank/absent fields are **omitted** so email-derived defaults survive.
- **`apps/inbound_email/services.py::_create_ticket_from_email(..., *, overrides=None)`** — overrides folded into the **initial `Ticket(**kwargs)`** (NOT a second `save()`), so `initialize_sla` runs against the **final** priority and lifecycle/assignment bookkeeping stay correct. Preserves the `"email"` provenance tag while appending agent tags (deduped). **`_maybe_auto_assign` is SKIPPED when an explicit assignee override is supplied** (avoids double-count + stale assignment row). `overrides=None` → byte-identical to the legacy path.
- **`apps/inbox_hub/services.py::convert_to_ticket(...)`** — signature widened to the **full 9 fields** `subject/description/category/due_date/tags` + `queue/status/assignee/priority`; the old "create then patch with `save(update_fields=)`" block is **replaced** by a drop-None `overrides` dict passed into `_create_ticket_from_email`. **Correctness fix:** the old post-patch path seeded SLA against the wrong (model-default) priority; folding into creation fixes it.
- **Both API actions call the shared validator:** `apps/inbound_email/api_views.py::InboundEmailViewSet.create_ticket` (`POST /inbound-email/{id}/create-ticket/`, role ≤30, idempotent → 400 w/ `ticket_number`) and `apps/inbox_hub/views.py::HubEmailViewSet.convert_to_ticket` (`POST /inbox-hub/hub-emails/{id}/convert-to-ticket/`) both call `build_ticket_overrides(request.data, tenant)` then `**overrides` into the service. The legacy (never-parked) path is wrapped in `transaction.atomic()` (previously **not** atomic — fixed here).
- **✅ Reachability gap CLOSED.** `apps/inbox_hub/serializers.py::ConvertToTicketSerializer` is now **schema-only** (OpenAPI docs) — the action does NOT validate through it; it delegates to `build_ticket_overrides`. So `subject/description/category/due_date/tags` ARE now reachable through the Hub convert endpoint. (Contrast the prior doc, which said they were unreachable.)
- **`templates/pages/emails/list.html`** (+317/−20) — the `#createTicketModal` form (subject* / priority [default "medium"] / queue / status / assignee / category / description); `loadTicketMeta()` lazy-loads 4 dropdown sources in parallel, filters out closed statuses client-side, defaults assignee to `CURRENT_USER_ID`; preview "Create ticket" opens the form **after** the preview modal fully hides (Bootstrap dual-modal backdrop guard via `hidden.bs.modal`); idempotent-400 (`err.ticket_number`) treated as success.
- **`templates/pages/inbox_hub/list.html` + `static/js/inbox-hub.js` (`?v=10`)** — the cockpit "Convert" action now opens a full **`#ihConvertPanel` offcanvas** (TipTap rich description + tags + flatpickr due-date + queue/status/assignee/priority/category), POSTing the full override set to the Hub convert endpoint.
- **Tests** (`tests/test_inbox_hub.py`): `TestConvertToTicketParity` (legacy↔Hub field parity, idempotency, priority + field overrides land, provenance preserved) + `TestHubConvertOverrides` (full overrides applied, blank-subject falls back, `normal`→400 [HubEmail vocab ≠ Ticket], closed status → 400, malformed queue UUID → 400, non-member assignee → 400).

## Feature — Inbound-Email Attachments (UNCOMMITTED working-tree feature)

Surfaces + safely streams customer-sent email attachments on BOTH the agent Emails page and the Inbox-Hub cockpit, behaving identically via one shared helper module. Inbound files are saved to `default_storage` and described in **`InboundEmail.attachment_metadata`** (JSONField list of `{filename, content_type, size, storage_path}`).

- **`apps/inbound_email/attachments.py` [NEW]** — two helpers. `serialize_attachments(inbound, url_for)` turns metadata into renderable rows `{index, filename, content_type, size, is_image, url}` (URL is **index-addressed** via the `url_for(index)` callable — the raw storage path is never exposed). `stream_attachment(inbound, raw_index, *, force_download=False)` returns a `FileResponse`; bad/out-of-range index or missing file → **`Http404`** (not 500 — expected once an email is converted and bytes move to a Ticket `Attachment`). **Safe raster images** (`png/jpeg/gif/webp/bmp` — the `INLINE_IMAGE_TYPES` allowlist) are served inline for `<img>` embedding; **everything else (incl. SVG → script risk) is forced to download**, always with `X-Content-Type-Options: nosniff`.
- **Authed download `@action` on BOTH viewsets:** `InboundEmailViewSet.attachment` (`GET /inbound-email/{id}/attachment/?i=<n>[&dl=1]`) and `HubEmailViewSet.attachment` (`GET /inbox-hub/hub-emails/{id}/attachment/?i=<n>[&dl=1]`). Each runs `get_object()` first, so the surface's own permission stack gates the stream (Emails = `HasTenantPermission`; Hub = `IsHubEmailAccessible` department scoping). `?dl=1` forces download for any type.
- **Serializers:** list rows carry only `has_attachments` (bool) + `attachment_count` (int); the **detail** serializer adds the full `attachments` array with authed per-index download URLs pointing at its own surface's `attachment` action.
- **Tests:** `tests/test_inbound_email.py::TestInboundEmailAttachments` (~6) + `tests/test_inbox_hub.py::TestHubEmailAttachments` (~9) — inline-vs-forced delivery, `nosniff`, `?dl=1`, out-of-range/missing-file → 404, anon → 401/403, Viewer → 403.

## Inbox Hub (engine committed @ `9575577`; access refactor + convert-panel + attachments UNCOMMITTED — full Phase 1B engine + triage-cockpit frontend + DEPARTMENT-scoped access + agent email-inbox handoff)

Email-to-Queue triage workspace. Reshapes inbound-email flow so NEW messages land in a centralised Hub for agent triage instead of auto-creating tickets, then routes to a department, seeds SLA deadlines, and auto-assigns to an online agent. **Default: OFF** — the seam at `apps/inbound_email/services.py` forks on `TenantSettings.inbox_hub_enabled` (default `False`).

> **Backend vs frontend split:** the *backend* is the full engine (routing, presence-aware assignment, hold/drain, state machine, SLA breach task, **11 viewset actions** incl. claim/escalate/transition/note + read-only `context` + authed `attachment` stream). The *frontend* (`inbox-hub.js` **1,378 LOC** `?v=10` + `inbox_hub/list.html`) is a **triage cockpit** surfacing **convert (full offcanvas panel) / assign / dismiss** over the untriaged backlog (5 workload lenses) plus a per-email **customer-context card + attachment thumbnails** — it does NOT call claim/escalate/transition/note. The "real work" happens after triage, in the converted Ticket or the assigned agent's Emails page.
>
> **⚠️ Access is now DEPARTMENT-SCOPED (the binary `UserGroup` gate is GONE; `user_in_any_group` fully deleted).** `apps/inbox_hub/access.py` is the single source of truth (all gates use **`effective_role`**):
> - **`can_access_inbox_hub(membership, *, user, tenant)`** — Admin/Manager (`hierarchy_level ≤ 20`) → **always**; Viewer (`> 30`) → **never**; agent-tier (Team Lead/Agent/IT/HR, levels 21–30) → **conditional**: granted iff (a) member of ≥1 **active** `inbox_hub.Department` (`user_department_ids`), OR (b) the tenant has **zero active departments** (`tenant_has_departments` → **fall-open** safety valve for fresh setup), OR (c) they have ≥1 `HubEmail` assigned to them (`_has_assigned_hub_email` → **black-hole** safety valve so auto-assigned mail is reachable).
> - **Row visibility** — supervisors (≤20) see all rows; agent-tier rows scoped by twin helpers kept in lock-step: queryset `hub_rows_q(user, dept_ids)` = `(state=NEW AND (department∈dept_ids OR department IS NULL)) OR assignee=me`, and object-level `agent_can_see_hub_email(obj, user, dept_ids)`. So an agent sees **untriaged NEW mail for their department(s) + the unrouted shared pool + anything assigned to them** (assigned mail stays visible across the full lifecycle).
> - **Lockout surfaces** when denied: hidden sidebar entry + zeroed badge (`nav/views.py`) + 403 on the page (`_inbox_hub_access_required` → `pages/403.html`) + 403 on the API (`HubEmailPermission` + `IsHubEmailAccessible`).
> - **Rollout note:** on a tenant that already has departments, an agent who is NOT a Department member (and has no assigned mail) now **403s** — make triagers Department members. This refactor killed two prior holes: the auto-assign black-hole (mail assigned to a group-less agent was invisible) and the "any-group-member reads the whole backlog" over-grant.

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
        _create_ticket_from_email(inbound, tenant, contact, system_user)   # (Feature B adds overrides= here)
```
**Existing-thread replies always go straight to the matching ticket regardless of the flag** — the Hub triages NEW conversations only. **Footgun:** the local `settings` rebinds the module-level `from django.conf import settings`; harmless today but a latent trap.

### Models (8 — `apps/inbox_hub/models.py`, 431 LOC; 1 migration `0001_initial`)

- **`Department(TenantScopedModel)`** — `name`, `slug` (UniqueConstraint per tenant), `lead` (FK User PROTECT), `members` (M2M via `DepartmentMembership`), `default_queue` (FK `tickets.Queue` SET_NULL), `business_hours` (FK SET_NULL), `is_active`. Index `(tenant, is_active)`.
- **`DepartmentMembership(TenantScopedModel)`** — through-model. `department`, `user`, `skills` (JSON list — **seeded-but-unused**, Phase-3 placeholder). UniqueConstraint `(department, user)`.
- **`HubEmail(TenantScopedModel)`** — the workspace entity. `inbound` 1:1 to `InboundEmail` CASCADE (`related_name="hub_email"`), `contact`, `department`, `queue`, `assignee` (all db_indexed). **9-state enum** (`NEW → ASSIGNED → IN_PROGRESS → PENDING_AGENT ⇄ AWAITING_CUSTOMER → ESCALATED → RESOLVED → CONVERTED_TO_TICKET | DISMISSED`) + **4-priority enum** (`low/normal/high/urgent` — **NO "medium"**; contrast: Reminder HAS medium). SLA fields (`sla_response_due_at` db_indexed, `sla_resolution_due_at`, `response_breached`, `resolution_breached`, `first_responded_at` ⚠read-never-written, `first_assigned_at`, `pause_started_at`/`total_pause_seconds` ⚠unused). Escalation: `escalation_count`, `escalated_to`. Terminal: `converted_ticket` (1:1 Ticket SET_NULL, `related_name="origin_hub_email"`), `dismissed_at`/`by`/`reason`. `auto_classification_data` JSONField (AI drop-zone; also the `sla_warning_sent` dedup store). `tags` JSONField (in serializer but **never written**). **5 indexes**: 4 composite `(tenant, …, state)` + partial `ih_email_active_sla_due` on `(tenant, sla_response_due_at)` filtered to active states (⚠ the partial-index condition does NOT include `escalated`, but the breach task DOES scan ESCALATED).
- **`HubEmailAssignment(TenantScopedModel)`** — immutable audit row. `Reason` enum (`AUTO/MANUAL/ESCALATION/REASSIGNMENT` — **ESCALATION never emitted**: `escalate_hub_email` creates no assignment row). `assigned_to` PROTECT, `assigned_by` null=system.
- **`HubEmailNote(TenantScopedModel)`** — internal note (`ordering=["created_at"]` ASC; not carried over on conversion). `author` PROTECT.
- **`HubEmailSLA(TenantScopedModel)`** — per-(queue, priority) or per-(department, priority) policy with `response_minutes`/`resolution_minutes`/`escalation_minutes` (escalation_minutes **unused**). Both FKs nullable; **conditional UniqueConstraints** scope uniqueness to non-null queue/department.
- **`RoutingRule(TenantScopedModel)`** — ordered IF/THEN rule. `match` JSON: `{sender_domain[], subject_regex, recipient_local[], keyword[]}` (keys AND, values OR). Outputs: `department`, `queue`, `category`, `priority`, `stop_on_match` (default True).
- **`QueueRouting(TenantScopedModel)`** — 1:1 supplement to `tickets.Queue`. `strategy_code` (default `"availability_aware|least_loaded|round_robin"`) + `leave_unassigned_when_no_match` (**unused** — AssignmentEngine never reads it).

### State machine (`state_machine.py`)

`can_transition(old,new)` is False if equal; `assert_transition` raises `ValueError`. Allowed (abridged): `NEW→{ASSIGNED,ESCALATED,DISMISSED,CONVERTED}`; `ASSIGNED→{NEW,IN_PROGRESS,PENDING_AGENT,AWAITING_CUSTOMER,ESCALATED,RESOLVED,DISMISSED,CONVERTED}`; the in-flight states reach RESOLVED/CONVERTED/DISMISSED; `ESCALATED→{ASSIGNED,IN_PROGRESS,RESOLVED,DISMISSED,CONVERTED}`; `RESOLVED→{IN_PROGRESS,CONVERTED,DISMISSED}`; `CONVERTED`/`DISMISSED` terminal. **`convert_to_ticket`, `dismiss_hub_email`, `assign_to`, `reassign_hub_email` set `state` directly (no `assert_transition`)**; only `transition_hub_email` enforces it.

### Engine

**`_post_park_hooks`** (scheduled via `transaction.on_commit` from `park_email_in_hub`, each step try/except-isolated): (1) `RoutingEngine.classify_and_route`, (2) `_initialize_hub_sla` (wall-clock deadlines, queue+priority → dept+priority `HubEmailSLA`; `escalation_minutes` never read), (3) `AssignmentEngine.try_assign` (only when `settings.inbox_hub_auto_assign`, default True).

**`RoutingEngine`** (`routing.py`, NOT a Channels route): `RoutingRule.unscoped.filter(tenant, is_active=True).order_by("order","id")`. Match keys AND / values OR; `sender_domain` exact-or-`.subdomain`; `keyword` substring over `subject\nbody`; `subject_regex` IGNORECASE against subject only (invalid regex → clause fails closed + warns). **Empty `match` matches nothing** (never a catch-all). Last-matched rule's non-null outputs win; `stop_on_match` breaks. Fallback dept = `TenantSettings.inbox_hub_default_department` (if active) else the single active Department iff exactly one exists; queue falls back to `department.default_queue`. Writes `EMAIL_CATEGORISED` (+`EMAIL_QUEUED` if queue changed), broadcasts `hub_email.transitioned`. State untouched; early-returns if nothing changed.

**`AssignmentEngine`** (`assignment.py`): string-token strategies as sort-key tie-breakers: `availability_aware` (most spare capacity) → `least_loaded` (active HubEmail count + `current_ticket_count`) → `round_robin` (least-recently-assigned). `DEFAULT_STRATEGY` overridable per-queue via `QueueRouting.strategy_code`.
- `_candidate_user_ids`: department members (or, if no department, `TenantMembership.objects` at `role__hierarchy_level == 30` — ⚠ **raw role, not effective_role**), then filtered by `is_assignable`.
- `try_assign` — **if none online → "held"** (stays NEW/unassigned) + `_notify_hold` nudges the dept lead (reuses `HUB_EMAIL_ASSIGNED` with a `data.held` flag).
- `assign_to` — atomic `select_for_update`; concurrency guard (bails if already assigned or not NEW); locks `AgentAvailability` + re-checks `is_assignable` when online required; sets assignee, `first_assigned_at`, state→ASSIGNED; creates `HubEmailAssignment(AUTO)`; bumps `current_ticket_count`; writes `EMAIL_AGENT_ASSIGNED`; broadcasts `hub_email.assigned`. **Does NOT touch `inbound.assignee`** (auto-assign ≠ email handoff).
- `drain_department_backlog(user, tenant)` — on agent (re)connect, assigns oldest held NEW/unassigned emails in their department(s) **or department-less**, up to `remaining_capacity`. Called from `presence._maybe_drain`. Respects `inbox_hub_auto_assign`.

**Presence layer** (`apps/agents/presence.py` + `models.py` + `tasks.py`):
- `AgentAvailability.last_seen` (DateTime, db_indexed, mig `agents/0007`). `DEFAULT_PRESENCE_TTL_SECONDS = 90`.
- **`is_assignable`** (the single auto-assign gate) — `status == ONLINE` AND `remaining_capacity > 0` AND `presence_fresh` (last_seen within `AGENT_PRESENCE_TTL_SECONDS`) AND (if `auto_away_outside_hours`) within working hours (⚠ uses **server-local** time, not per-tenant tz; fail-open on misconfig). **Don't confuse with `is_available`** (looser — omits the freshness check).
- `touch_presence(user, tenant)` — `get_or_create` on `.unscoped`; stamps `last_seen`; auto-promotes **OFFLINE→ONLINE only** when `AGENT_PRESENCE_AUTO_ONLINE` (never overrides manual AWAY/BUSY); uses `.update()` (no signal churn).
- `handle_live_heartbeat(user, tenant, *, is_connect=False)` — sync entry from `LiveEventConsumer`; stamps presence, broadcasts `agent.presence` on change, drains held backlog on any (re)connect/status change. Error-swallowing.
- `agents.tasks.reap_stale_presence` (`@shared_task`, kanzan_default) — flips `ONLINE` rows whose `last_seen` is NULL or older than TTL to `AWAY`, broadcasts per row, `iterator(chunk_size=200)`. **Beat: 60s.**

**`apps/inbox_hub/tasks.py::check_hub_sla_breaches`** (`@shared_task`, kanzan_default; **Beat 120s**) — cross-tenant `.unscoped`. Sweeps `state ∈ {NEW,ASSIGNED,IN_PROGRESS,PENDING_AGENT,ESCALATED}` with a response deadline. Flags response breaches (auto-escalates via `escalate_hub_email`), fires a one-shot warning `HUB_SLA_WARNING_MINUTES` (default 15) before deadline (deduped via `auto_classification_data["sla_warning_sent"]`), flags resolution breaches (flag-only, no escalate). **⚠ `first_responded_at` is NEVER written anywhere** — the response-breach guard `first_responded_at is None` is always True, so the breach always fires on the deadline (no concept of a reply marking SLA met) and every breach also auto-escalates.

### Service layer (`apps/inbox_hub/services.py`, ~546 LOC)

All write polymorphic `ActivityLog` rows and broadcast LiveBus on commit. **All 8 `EMAIL_*` ActivityLog actions + all 5 `HUB_EMAIL_*` Notifications are emitted.** (Stale module docstring still says "Phase 1A — out of scope".)
- `park_email_in_hub` — idempotent `get_or_create(inbound=…)`; `EMAIL_RECEIVED`; broadcasts `hub_email.created` (immediate); schedules `_post_park_hooks` on_commit.
- `convert_to_ticket(...)` — idempotent; reuses `_create_ticket_from_email` (Feature B widened its overrides); state→CONVERTED; `EMAIL_CONVERTED_TO_TICKET`; broadcasts `hub_email.converted_to_ticket`.
- `dismiss_hub_email(...)` — idempotent; state→DISMISSED; `EMAIL_DISMISSED`.
- `transition_hub_email(...)` — `assert_transition`; `STATUS_CHANGED`; `hub_email.transitioned`.
- `escalate_hub_email(...)` — `escalation_count += 1` **(bumped even when the ESCALATED transition is illegal)**, `escalated_to`=dept lead, state→ESCALATED only if legal; `EMAIL_ESCALATED`; `_notify_escalation` (`HUB_EMAIL_ESCALATED_TO_ME`, deep-links `/inbox-hub/`).
- `reassign_hub_email(...)` — `select_for_update`; online NOT required (manual override); `EMAIL_REASSIGNED`/`EMAIL_AGENT_ASSIGNED`; `_notify_reassignment` (deep-links `/emails/`). **Also stamps `inbound.assignee = new_user` + `inbox_status=PENDING` + `is_read=False`** — the agent email-inbox handoff backing `assign`/`reassign`/`claim`.
- `add_hub_email_note(...)` — creates `HubEmailNote`; broadcasts `hub_email.transitioned`.

### API surface (`apps/inbox_hub/views.py` + `urls.py`)

`HubEmailViewSet` (list/retrieve only) permission stack `[IsAuthenticated, IsTenantMember, HubEmailPermission, IsHubEmailAccessible]`; `get_queryset` `select_related(...)` + `prefetch_related("notes","notes__author")`. **Agent-tier row filter (uses `effective_role`):** for `level > 20`, applies `hub_rows_q(user, dept_ids)` = `(state=NEW AND (department∈my-active-depts OR department IS NULL)) OR assignee=me` (supervisors ≤20 see all). Chips: `assignee=me|unassigned|<uuid>`, `state`/`priority`/`queue`/`department`, **`sla_risk=true`** (`sla_response_due_at IS NOT NULL AND response_breached=False`).

| Action | Method / URL | Codename | Service / note |
|---|---|---|---|
| `list`/`retrieve` | `GET /hub-emails/[{id}/]` | `hub_email.view` | — |
| **`context`** | `GET /{id}/context/` | `hub_email.view` | `contacts.context.build_contact_context` — served off the Hub viewset (NOT `/contacts/`) so agents can reach a freshly-parked contact |
| **`attachment`** | `GET /{id}/attachment/?i=<n>[&dl=1]` | `hub_email.view` | `attachments.stream_attachment(obj.inbound, i)` — authed customer-attachment stream (inline raster / forced download + `nosniff`) |
| `convert_to_ticket` | `POST /{id}/convert-to-ticket/` | `hub_email.convert` | → `{ticket, hub_email}` 201 (✅ now carries the **full 9-field** override set via shared `build_ticket_overrides` — gap CLOSED) |
| `dismiss` | `POST /{id}/dismiss/` | `hub_email.dismiss` | `dismiss_hub_email` |
| `assign` | `POST /{id}/assign/` | `hub_email.assign` | `reassign_hub_email` (validates member) |
| `reassign` | `POST /{id}/reassign/` | `hub_email.reassign` | `reassign_hub_email` |
| `claim` | `POST /{id}/claim/` | **none** — agent-level (≤30) | `reassign_hub_email(self)` |
| `escalate` | `POST /{id}/escalate/` | `hub_email.escalate` | `escalate_hub_email` |
| `transition` | `POST /{id}/transition/` | **none** — agent-level (≤30) | `transition_hub_email` (`ValueError`→400) |
| `note` | `POST /{id}/note/` | `hub_email.note` | `add_hub_email_note` → 201 |

**No `reply` action** despite the `hub_email.reply` codename being seeded (dead codename). **`claim`/`escalate`/`transition`/`note` are live backend endpoints the cockpit never calls** (dead-but-present surface). **4 config viewsets** (`ModelViewSet`): `DepartmentViewSet` (+add/remove-members; list/retrieve open to members, writes `IsTenantAdminOrManager`), `RoutingRuleViewSet` (+reorder, manager-gated), `HubEmailSLAViewSet`, `QueueRoutingViewSet` (manager-gated).

**`HubEmailPermission`** (`permissions.py`) — a **local** permission class (NOT the global `ACTION_MAP` — `assign`/`escalate` collide with `TicketViewSet`). Uses `effective_role`. **First gate = the new department access gate**: `if not can_access_inbox_hub(membership, user, tenant): return False`. Then `AGENT_LEVEL_ACTIONS = {claim, transition}` gated `≤30`; else per-action codename; explicit-perm check or hierarchy fallback: `view ≤40`, `convert/reply/escalate/note ≤30`, `assign/reassign/dismiss ≤20`. **`IsHubEmailAccessible` row-scope:** delegates to `agent_can_see_hub_email` — `≤20` all rows; agent-tier (`21–30`) → `assignee=me OR (state=NEW AND department∈my-depts/NULL)`; Viewer (`>30`) never.

### RBAC (12 codenames — `apps/accounts/defaults.py` + mig `accounts/0012`) + department access gate

- `hub_email.{view, assign, reassign, convert, dismiss, reply, escalate, note}` (8) + `department.{view, manage}` (2) + `routing_rule.manage` (1) + `hub_sla.manage` (1).
- Grants: Admin/Manager = all 12; Team Lead = agent-tier (`view/convert/reply/escalate/note` + `department.view`) **plus** `assign/reassign/dismiss`; Agent/IT/HR = agent-tier only; Viewer = `view` via ≤40 fallback only.
- `accounts/0012` uses `role.permissions.add(*perms)` (not `.set()`) so operator customisations survive. **The codename grants are SUBORDINATE to the department access gate** — a group-less / department-less agent (no assigned mail, tenant has departments) is locked out regardless of codenames; Admin/Manager (≤20) are always in.

### Agent email-inbox handoff (`InboundEmail.assignee`)

When a HubEmail is **manually** assigned, the original customer message is handed to that agent's personal Emails page.
- **`InboundEmail.assignee`** FK (SET_NULL, db_indexed, `related_name="assigned_inbound_emails"`, mig `inbound_email/0010`) + index `email_tenant_assignee_idx`. `assignee` is **mutable** (the `save()` immutability guard covers only `linked_*`/`actioned_*`).
- **Set only by `reassign_hub_email`** (assign/reassign/claim). **`AssignmentEngine.assign_to` (auto-assign) does NOT touch `inbound.assignee`** — only `he.assignee`. So auto-assigned mail stays in the Hub; only manual claim/assign/reassign reaches the agent's `/emails/?assigned=me`.
- **Consumer side — `apps/inbound_email/api_views.py`**: `InboundEmailViewSet.get_queryset` query-param branches — `?assigned=me` (bypasses internal/customer split + bounce-hiding), `?internal=true` (excludes `sender_type=CUSTOMER`), `?mine=true` (`recipient_email__iexact=user.email`, drops OUTBOUND/SYSTEM); default hides BOUNCED unless `?status=` or `?include_bounces=true`. The **`create_ticket` action** `POST /inbound-email/{id}/create-ticket/` (`get_permissions` swaps to `[IsAuthenticated, IsTenantMember]`; handler enforces `effective_role ≤ 30`; idempotent → 400 if `ticket_id` set): if `email.hub_email` exists → `convert_to_ticket`; else `_create_ticket_from_email` (Feature B wires overrides into both).
- **`templates/pages/emails/list.html`**: "Assigned to me" stat tab; dual-source load = `Promise.all` of `?internal=true&mine=true` + `?assigned=me`, merged/deduped by id; "Create ticket" button → the `create-ticket` action (Feature B's form).

### Configuration & seeding

- **`TenantSettings`** fields: `inbox_hub_enabled` (default **False**, mig 0009), `inbox_hub_auto_assign` (default **True**, mig 0010; False = manual claim only), `inbox_hub_default_department` (FK SET_NULL, mig 0010). Two toggles in `settings/tenant.html`.
- **Settings constants** (`main/settings/base.py`, env-overridable): `AGENT_PRESENCE_TTL_SECONDS=90`, `AGENT_PRESENCE_AUTO_ONLINE=True`, `HUB_SLA_WARNING_MINUTES=15`.
- **`manage.py seed_inbox_hub_defaults [--tenant-slug <slug> | --all-tenants]`** — seeds **one "General" Department** (lead = lowest-hierarchy active member; default queue = "General"/"Support" else oldest; enrolls active non-viewer members), points `inbox_hub_default_department` at it. Idempotent. **Does NOT seed** RoutingRules/HubEmailSLA/QueueRouting.

### Known gaps / footguns (Inbox Hub)

- **Auto-assign ≠ email handoff** — `assign_to` sets only `HubEmail.assignee`, never `InboundEmail.assignee`.
- **Dead-but-present surface** — backend `claim`/`escalate`/`transition`/`note` actions + `hub_email.reply` codename are live but unused by the cockpit.
- **`first_responded_at` read-but-never-written** → response-breach always fires on the deadline (→ also auto-escalates every breach).
- **`escalate_hub_email` increments `escalation_count` even when the ESCALATED transition is illegal.** `HubEmailAssignment.Reason.ESCALATION` seeded-but-never-emitted.
- **Seeded-but-unused fields**: `DepartmentMembership.skills`, `HubEmail.tags`/`pause_started_at`/`total_pause_seconds`, `QueueRouting.leave_unassigned_when_no_match`, `HubEmailSLA.escalation_minutes`.
- **`InboundEmail.Status.PARKED_IN_HUB` is write-dead** — `park_email_in_hub` never sets it; parked mail keeps `PROCESSING`. `?status=parked_in_hub` returns nothing.
- **Stale module docstrings** in `inbox_hub/{urls,services,serializers}.py` reference "Phase 1A".
- **`convert_to_ticket`/`dismiss_hub_email` bypass the state machine** (set state directly).
- **`_candidate_user_ids` no-department fallback uses raw `role`, not `effective_role`.**
- **Worktree gap (Feature B)**: `convert_to_ticket` service widened ahead of its serializer+viewset → 5 override keys unreachable via the Hub convert endpoint (reachable only via the Emails-page `create_ticket`).
- Still future: business-hours-aware Hub SLA (currently wall-clock); `auto_classification_data` AI classification; historical backfill when the flag flips ON; auto-seed of a default Department on tenant creation; RoutingRule/HubEmailSLA/QueueRouting seeding.

## Models (91 model classes across 21 apps with models.py)

### Base Models (Abstract)
- **TimestampedModel**: UUID PK + `created_at` + `updated_at`; default ordering `["-created_at"]`.
- **TenantScopedModel**: TimestampedModel + `tenant` FK (CASCADE, editable=False, db_index=True) + auto-filtering.

### Tenants / Accounts

**tenants** (2): `Tenant` (name, slug unique, domain unique nullable, is_active, logo); `TenantSettings` (1:1; auth_method, SSO config, timezone, date_format, branding `primary_color="#6366F1"`/`accent_color="#F59E0B"` with hex validators, `inbound_email_address` unique, business hours/days, `auto_close_days` 5, `csat_delay_minutes` 60, `auto_transition_on_assign`, `auto_send_ticket_created_email`, `auto_assign_inbound_email_tickets` default False, **`inbox_hub_enabled`** False, **`inbox_hub_auto_assign`** True, **`inbox_hub_default_department`** FK).

**accounts** (8): `User(AbstractUser)` (email-based, UUID PK, `username=None`, `auth_version`, `avatar`, `phone`, `is_service_account`). `Permission` — **global** (not tenant-scoped); `Action` enum (7: view/create/update/delete/assign/export/manage). ⚠ The inbox-hub verbs (`convert/dismiss/reassign/reply/escalate/note`) are NOT in `Permission.Action` but are stored as the `action` column value — choices aren't DB-enforced. `Role(TenantScopedModel)` — M2M `permissions`, `hierarchy_level` default 100, `is_system`. `Profile(TenantScopedModel)` (per-tenant prefs incl. free-text `department` string — distinct from inbox_hub `Department`). `TenantMembership` — NOT TenantScoped; UUID PK; FKs `user`/`tenant`/`role` (PROTECT)/`temporary_role`/`temporary_role_granted_by`/`invited_by`; **M2M `temporary_permissions` → Permission** (empty = full temp-role perms; non-empty = intersection); methods `has_active_temporary_role`, `effective_role`, `get_effective_permissions_qs()`, `has_effective_permission(codename)`. `Invitation(TenantScopedModel)`. `UserGroup(TenantScopedModel)` (M2M `members`; **NOT in admin**; "one user per group" enforced in serializer + viewset with a **flat-string `detail`** error; ⚠ docstring "does not grant permissions or route work" is **stale** — it now gates the Inbox Hub). `EmailVerificationToken` (global).

### Tickets (22 model classes — heaviest app)

`Pipeline`, `PipelineStage` (`is_won`/`is_lost`, `probability`), `TicketStatus` (incl. `pauses_sla`, `is_closed`, `is_default`), `Queue` (`default_assignee`, `auto_assign`, **`department` FK → inbox_hub.Department SET_NULL** — mig 0027, nullable opt-in), `TicketCategory`, `TicketCounter` (NOT TenantScoped; OneToOne tenant; `next_number()` SELECT-FOR-UPDATE + F-expression — ⚠ no-op on SQLite), `Ticket` (~80 fields; soft delete; CSAT; deal fields; `merged_into`; `pre_wait_status`; `tags`+`custom_data` JSON). **`Ticket.save()` auto-populates `company_id`** from the linked Contact's company when not explicitly set (via `Contact.unscoped`; never overwrites an explicit company). `Ticket.clean()` validates assignee membership but is **not** called by `save()`. `TicketLink` (4 types + circular guard via BFS over `blocks`), `SLAPolicy`, `EscalationRule`, `BusinessHours` (IANA tz + schedule JSON keyed "0".."6"), `PublicHoliday`, `SLAPause` (`duration_minutes`), `TicketActivity` (**27 `Event` choices** — the inner class is `Event`, NOT `EventType`; `EventType` belongs to `Webhook`), `CannedResponse`, `Macro`, `SavedView`, `TicketAssignment` (immutable audit), `TicketWatcher` (4 reasons, `is_muted`), `TimeEntry`, `TicketTemplate`, `Webhook` (HMAC SHA-256, 8 EventType, auto-disable at 10 failures).

### Contacts (5)

`Account` (`mrr`, `health_score` default 50, ⚠ clamped only in `clean()` which save() doesn't call), `Company` (name unique per tenant; `custom_data` JSON ⚠**never synced to CustomFieldValue** — no Company sync signal), `Contact` (email unique per tenant, `email_bouncing` indexed, `lead_score` default 50 ⚠no clamp at all, `last_activity_at` indexed — written only via `.update()` so no signal, `source` 6-choice), `ContactGroup` (M2M contacts), `ContactEvent` (append-only 360° timeline; intentionally NOT live-broadcast; NOT in admin). `build_contact_context` in `context.py` (cache prefix `contact_context_v2`, 60s TTL, cache skipped when `exclude_ticket` given) — shared by `ContactViewSet.context` and `HubEmailViewSet.context`.

### CRM (2)

`Activity` (call/email/meeting/task; ⚠ docstring claims save() bumps `ticket.last_activity_at` but it's the viewset's `_touch_ticket` — direct `.objects.create` bypasses it). `Reminder` (formerly Recall; **M2M `contacts`/`tickets`**; `Priority` 4-choice incl. **MEDIUM** default — differs from HubEmail's no-medium; `status` is a **derived property**; custom `ReminderManager`/`ReminderUnscopedManager` from `ReminderQuerySet` with `.overdue()/.pending()/.for_user()`; `mark_completed/mark_cancelled/reschedule`; **+ `due_notified_at`** watermark — Feature A).

### Inbound Email (3)

`InboundEmail` extends `TimestampedModel` (NOT TenantScopedModel — tenant nullable, resolved post-parse → default `objects` is a PLAIN manager, cross-tenant; every query must filter `tenant=` explicitly). `Status` (**9 members** incl. `PARKED_IN_HUB` ⚠write-dead). `Direction`/`SenderType`/`InboxStatus`/`InboxAction`. **`assignee` FK** (SET_NULL, db_indexed, mig 0010, **mutable**). **`attachment_metadata` JSONField** — list of `{filename, content_type, size, storage_path}` for customer-sent files (surfaced + streamed by `apps/inbound_email/attachments.py`; storage path never exposed — index-addressed download). Threading: `message_id` (indexed, stored without `<>`), `in_reply_to`, `references`. Idempotency keys: `"in:{tenant}:{mid}"` / `"out:{tenant}:{ticket}:{mid}"` (unique). `save()` enforces immutability of `linked_at/by` + `actioned_at/by` (does a fresh SELECT on every update). `BounceLog`, `IMAPPollState` (uid_validity + last_uid watermark). **Only InboundEmail in admin.** 6 indexes.

### Knowledge (6)

`Category`, `Article` (status 5-choice DRAFT/PENDING_REVIEW/PUBLISHED/REJECTED/FLAGGED; visibility internal/public; Postgres FTS via `SearchVectorField`+GinIndex — ⚠ a **dev no-op** since `update_search_vector` early-returns on non-Postgres; **`allowed_groups` M2M to UserGroup** = audience gate independent of `visibility`; **`tags` JSON IS live** read/write/`tags__contains`-filter — unlike HubEmail.tags; auto-slug with collision suffix via `Article.unscoped`; **`save()` resolves tenant** from context before slug scan; ⚠ two confusingly-named date fields `reviewed_at` [workflow] vs `review_at` [stale-content scheduler]). `KBRevision` ⚠**dead-write**, `KBVote` (session_key-keyed, anon portal voting), `KBSearchGap`, `KBTicketLink` ⚠**dead-write**. **Only Category + Article in admin.**

### Kanban (3)

`Board` (`resource_type` TICKET/DEAL, `is_default`, **`is_personal`** — private to creator, no status writeback), `Column` (board, order, optional `status` FK, wip_limit, color), `CardPosition` (polymorphic GenericFK; unique on column+content_type+object_id).

### Comments / Messaging / Newsfeed / Notifications / Inbox Hub

**comments** (4): `Comment` (polymorphic GenericFK, threaded via self-FK `parent`, `is_internal`), `Mention` (plain model), `CommentRead` (plain model — row existence = read), `ActivityLog` (**34 action choices** — 26 core + 8 `EMAIL_*` for Inbox Hub). Comment + Mention + ActivityLog in admin; **CommentRead NOT**.

**messaging** (3): `Conversation` (DIRECT/GROUP/TICKET — no default; FK `source_group` to UserGroup, dedupes per-creator group convs), `ConversationParticipant` (plain model, no tenant col; `last_read_at`), `Message` (`body` **required at model level** but `MessageCreateSerializer.body` allow_blank for attachment-only; null author = system; threaded; mentions M2M; `is_edited`; attachments via GenericFK). **✅ Cross-tenant user enumeration/injection FIXED (Sprint 0)** — DM/group creation + mentions + WS `_create_message` now reject/scope `user_ids` to active tenant members (see §Sprint 0 Hardening #2).

**newsfeed** (3): `NewsPost` (5 categories; `author` **CASCADE** — deleting user nukes posts; per-post free-text `emoji`), `NewsPostReaction` (6 emoji; one per user per post), `NewsPostRead` (plain model, no tenant col — row existence = read). **All 3 in admin.**

**notifications** (2): `Notification` (**21 NotificationType** — +5 `HUB_EMAIL_*` + the new `REMINDER_DUE`). **NOT polymorphic** — only a `data` JSONField + `recipient` FK. `NotificationPreference` (no row ⇒ both channels ON). Single creator `send_notification(...)`; `INTERNAL_ONLY_TYPES` (5) force email off.

**inbox_hub** (8): see §Inbox Hub above.

### Agents / Custom Fields / Billing / Analytics / Attachments / Notes / API Keys / VoIP

**agents** (2): `AgentAvailability` (online/away/busy/offline + `custom_status` FK; load + working-hours JSON + `auto_away_outside_hours`; **+`last_seen` mig 0007 + `presence_fresh`/`is_assignable`/`is_available`/`remaining_capacity` properties**); `CustomAgentStatus` (tenant-scoped; `StatusColor` 8-choice; NOT in admin). `BUILTIN_STATUS_SLUGS` frozenset.

**custom_fields** (2): `CustomFieldDefinition` (8 field types × **3 modules: ticket/contact/company**; M2M `visible_to_roles`), `CustomFieldValue` (EAV; 4 typed value columns). **⚠ Sync signals exist only for Ticket + Contact — NOT Company** (so Company custom fields never produce `CustomFieldValue` rows).

**billing** (4): `Plan` (global; tiered + `has_voip`/`has_call_recording`/`max_calls_per_month` — **model defaults False/False/NULL, but ✅ the seeder now SETS them (Sprint 0)**: Free off, Pro `True/True/1000`, Enterprise `True/True/None`; mig `billing/0003` backfills existing rows), `Subscription` (1:1 Tenant; 6 status; `is_active`/`in_grace_period` properties; **✅ webhook now repoints the OneToOne row on re-subscribe** — no IntegrityError), `Invoice`, `UsageTracker`. **No `apps/billing/tasks.py`** (the `kanzan_webhooks` route for billing tasks is dormant — file doesn't exist). `decorators.py::require_feature` is **100% dead** (zero call sites). `SubscriptionMiddleware` → HTTP 402 when neither active nor in grace (own 14-entry exempt list; fail-open on no tenant/no subscription).

**analytics** (4): `ReportDefinition`, `DashboardWidget` (null user = shared), `ExportJob` (CSV/XLSX/PDF — ⚠ **PDF → CSV bytes in a `.csv` filename**; **XLSX-without-openpyxl → CSV bytes in a `.xlsx` filename**), `CalendarEvent`. **⚠ `DashboardView` has only `IsAuthenticated`** (no `HasTenantPermission` — bypasses resource RBAC; tenant isolation still holds via scoped querysets). `process_export_job.delay()` not wrapped in `on_commit` (early-run can strand a job at `pending`).

**attachments** (1): `Attachment` (polymorphic GenericFK with **UUIDField object_id** — only UUID-PK models attachable; `tenants/{id}/attachments/YYYY/MM/{12hex}_{filename}`; **python-magic true-MIME validation** ignoring client Content-Type, 25MB cap, allowlist; cross-tenant validated in serializer). **✅ Object-level authz added (Sprint 0)** — `apps/attachments/access.py::can_access_target` + `CanAccessAttachmentObject` perm + authed `download/` action gate retrieve/download/upload/destroy by the *target object's* visibility (tickets/comments/messages explicit; other targets allow). ⚠ raw `/media/` still unauthenticated — see §Sprint 0 Hardening #3.

**notes** (1): `QuickNote` (6 colors; pinning; per-user). No signals.

**api_keys** (1): `APIKey(TenantScopedModel)` — `name`, `service_user` (1:1 hidden `User` with `is_service_account=True`), `role` (FK PROTECT — drives `HasTenantPermission`), `prefix` (indexed, = `cleartext[:20]`), `hashed_key` (**SHA-512** hex; cleartext never persisted), `created_by` PROTECT, `is_active`, `expires_at`, `last_used_*`, `request_count`. Cleartext format: `kz_live_<slug6>_<token_urlsafe(32)>`. `regenerate` rotates in place; `revoke` soft-disables key+service-user+membership.

**voip** (5): `VoIPSettings` (singleton; encrypted ARI creds; STUN/TURN; `pjsip_context`; **`is_active` — NOT `Plan.has_voip` — drives softphone UI visibility**), `Extension` (sip_username **globally unique**, encrypted password), `CallLog` (3 directions, 9 statuses, indexed `asterisk_channel_id`), `CallRecording` (1:1 CallLog), `CallQueue` (5 ACD strategies + M2M Extension members).

### Polymorphic (GenericFK) Models — 5 total

`Attachment`, `Comment`, `ActivityLog`, `CustomFieldValue`, `CardPosition`. **Not** Notification (data JSONField only).

## Role-Based Access Control

**Hierarchy:** Admin(10) → Manager(20) → **Team Lead(25)** → Agent(30) / **IT(30)** / **HR(30)** → Viewer(40).

**Default role seeding (`apps/tenants/signals.py::create_default_roles`)** runs on `Tenant.post_save (created=True)` and seeds **all seven** system roles inline (all `is_system=True`); permission sets for the **six** perm-bearing roles come from `apps/accounts/defaults.py::ROLE_DEFINITIONS` (**6 entries** — Viewer is intentionally permission-less, leans on the ≤40 view fallback). `ALL_CODENAMES` = **69 unique codenames** (12 inbox-hub-related). ⚠ The signal path uses `Role.objects.get_or_create` during `post_save` with **no tenant context bound** — masked by the explicit `tenant=instance` arg + DB unique constraint; it diverges from `defaults.provision_default_roles` (which correctly uses `Role.unscoped` but is **dead** — no runtime callers; permissions are seeded by migrations `accounts/0011`/`0012`).

- `is_admin`: `≤10`; `is_admin_or_manager`: `≤20`; `is_agent_or_above`: `≤30`. **Team Lead (25)** satisfies `is_agent_or_above` but NOT `is_admin_or_manager` (so `_role_required(20)` pages deny Team Lead by design). Viewer (40) satisfies none (≤40 view-fallback only). IT/HR share level 30 with Agent (+`user.view`).
- **Always use `TenantMembership.effective_role`** — temporary role wins until `temporary_role_expires_at`. ⚠️ **Mixed `role` vs `effective_role` drift (pre-existing footgun):** permission classes (`HasTenantPermission`, `IsTicketAccessible`, `IsTenantAdmin*`), nav badges, and the Hub permission classes use `effective_role`; but the **ticket-list queryset** (`tickets/views.py` — many sites), **`analytics/services.py`**, **`kanban/serializers.py::_get_allowed_ticket_ids`**, **`apps/agents/services.py::pick_email_agent`**, and the Hub **`_candidate_user_ids`** no-department fallback still gate on **raw `role.hierarchy_level`**. A temp-promoted agent gets object-perms + badges as a manager but is still list/kanban/analytics-filtered (and email-routed) as an agent.
- **`AgentAvailabilityViewSet.assignable_roles`** **excludes the `admin` slug** to prevent privilege escalation through the UI. `grant_temp_role` sets `temporary_permissions.set([...])` (empty clears → full role).
- **Shared visibility modules** (single source of truth):
  - **`apps/tickets/access.py`** — `agent_visible_tickets_q(user)` = `Q(assignee=user) | (Q(created_by=user) & Q(assignee__isnull=True))` + object-level `agent_can_see_ticket`. **An agent sees a ticket only if assigned to them, or they created it AND it's still unassigned — a self-created ticket handed off LEAVES the creator's view.** Imported by `tickets/views.py` (list), `accounts/permissions.py::IsTicketAccessible` (object), `analytics/services.py`, `nav/views.py` (badge), `kanban/serializers.py` (cards). Admin/Manager (≤20) bypass.
  - **`apps/inbox_hub/access.py`** — **DEPARTMENT-scoped** Hub gate (the old `user_in_any_group` is DELETED). `can_access_inbox_hub(membership, *, user, tenant)` (Admin/Manager ≤20 always; Viewer >30 never; agent-tier iff in active dept OR tenant has no active depts OR has assigned mail) + `hub_rows_q` / `agent_can_see_hub_email` (twin row-scope helpers) + `user_department_ids` + `tenant_has_departments`. Imported by `inbox_hub/permissions.py`, `inbox_hub/views.py`, `tenants/frontend_views.py`, `tenants/context_processors.py`, `nav/views.py`.
  - **`apps/contacts/context.py`** — `build_contact_context(contact, tenant, *, exclude_ticket=None)`. Cache prefix `contact_context_v2`, 60s TTL, cache skipped when `exclude_ticket` given. Shared by `ContactViewSet.context` and `HubEmailViewSet.context`.
- **Permission classes** (`apps/accounts/permissions.py`):
  - `HasTenantPermission` — codename-based; `ACTION_MAP` maps ~95 DRF actions to `{resource}.{action}` (incl. `convert_to_ticket → convert`, `dismiss → dismiss`, `context → view`; `assign`/`escalate` deliberately remapped to `update` and NOT used by Inbox Hub — they collide with TicketViewSet). Uses `effective_role`; no `permission_resource` → **allow**; unmapped action → **deny**. Explicit-perm check first, then hierarchy fallback (`view ≤40`, `create/update ≤30`, else ≤20).
  - `IsTicketAccessible` — object-level (≤20 bypass; else `agent_can_see_ticket`).
  - `IsTenantMember` (⚠ returns `True` when `request.tenant is None` — for public/main-site endpoints), `IsTenantAdmin`, `IsTenantAdminOrManager`.
  - **`HubEmailPermission` + `IsHubEmailAccessible`** — Inbox Hub's local stack with the department access gate (`can_access_inbox_hub` + `agent_can_see_hub_email`).
- `_role_required(20)` gates admin/manager frontend pages (Team Lead 25 does NOT pass by design). `_role_required(30)` gates Emails. **`/settings/`** is `@_membership_required + @ensure_csrf_cookie` — any member can load; API enforces admin-only writes (per-field allowlist for Managers).

## Signals (10 apps with signals.py + notifications/signal_handlers.py — 42 receivers)

- **Tenants — 2:** `Tenant.post_save(created)` → `create_tenant_settings` + `create_default_roles` (7 system roles inline).
- **Accounts — 5:** `TenantMembership.post_save` → `create_profile_on_membership` + `broadcast_membership_save`; `post_delete` → broadcast; `Profile.post_save` → broadcast; `User.post_save` → `broadcast_user_save` (skips creation; fans across every active membership; emits avatar URL).
- **Tickets — 11:** `pre_save handle_ticket_status_change` (snapshots old values; stamps resolved/closed_at; resolution-breach check); `post_save` × `fire_ticket_created_signal`, `fire_ticket_assigned_signal`, `log_ticket_activity` (**2-sec dedup** via `_activity_already_logged`; reads `_skip_signal_logging`; ⚠ docstrings say "5-second" but code is 2s), `handle_sla_pause_on_status_change`, `create_kanban_card_on_ticket_save`, `sync_kanban_card_on_status_change` (by status FK), `sync_kanban_card_on_pipeline_stage_change` (⚠ by column **name** — rename breaks it silently); `post_delete remove_kanban_cards_on_ticket_delete` (hard-delete only); `@receiver(ticket_closed) check_kb_article_coverage`; `SLAPolicy.post_save propagate_sla_policy_change` (async via Celery if >50 tickets).
- **Custom Fields — 2:** `Ticket.post_save`/`Contact.post_save` → sync `CustomFieldValue` from `custom_data`. (⚠ No Company receiver.)
- **Knowledge — 1:** `Article.post_save update_search_vector` (PG FTS; `.update()`; early-returns on non-Postgres).
- **Notifications — 2 (`signal_handlers.py`):** `@receiver(ticket_assigned) handle_ticket_assigned` (skips self-assign); `@receiver(ticket_comment_created) handle_comment_notification` (+ `@email@domain` mention parsing + `_queue_contact_reply_email`, which skips when author email == contact email).
- **VoIP — 1:** `CallLog.post_save` on terminal status → `TicketActivity` + `ActivityLog` + queues `process_call_recording`.
- **Comments — 2:** `Comment.post_save/delete` → `broadcast_comment_save/delete` (⚠ broadcasts internal comment bodies tenant-wide).
- **Contacts — 8:** `Contact/Company/Account/ContactGroup × post_save/delete` (ContactEvent skipped).
- **CRM — 4:** `Activity/Reminder × post_save/delete` (Reminder verb resolved by state).
- **Newsfeed — 4:** `NewsPost/NewsPostReaction × post_save/delete`.

## Dual-Write Logging

**Two parallel log systems:**
1. **TicketActivity** — human-readable timeline, **27 `Event`s**. `/api/v1/tickets/tickets/{id}/timeline/`.
2. **ActivityLog** — polymorphic audit trail with diffs+IP, **34 actions** (26 core + 8 `EMAIL_*`). `/api/v1/tickets/tickets/{id}/activity/`.

**Dedup pattern:** `log_ticket_activity` checks `instance._skip_signal_logging` (set in 7 service sites + `views.py perform_update`, read in 1 signal site); a 2-sec window in `_activity_already_logged` is the safety net for paths that don't set the flag.

**Service layer** (`apps/tickets/services.py`, **20 public** functions of 27) — every mutation writes to BOTH logs atomically + broadcasts via `transaction.on_commit`: `broadcast_ticket_event`, `initialize_sla`, `log_sla_change`, `create_ticket_activity`, `assign_ticket`, `validate_status_transition`, `transition_ticket_status`, `resume_from_wait`, `change_ticket_status`, `close_ticket`, `escalate_ticket`, `change_ticket_priority`, `log_ticket_comment`, `bulk_update_tickets`, `record_first_response`, `merge_tickets`, `split_ticket`, `render_macro`, `apply_macro`, `transition_pipeline_stage`. **⚠ There is NO `create_ticket()` service fn** — creation is serializer-driven in `TicketViewSet.perform_create` (plan-limit check → `serializer.save()` → `create_ticket_activity`). `_create_ticket_activity` mirrors to `ContactEvent` (best-effort). **`ALLOWED_TRANSITIONS`** (slug-keyed): `open→[in-progress,waiting,resolved,closed]`, `in-progress→[open,waiting,resolved,closed]`, `waiting→[open,in-progress,resolved,closed]`, `resolved→[closed,open]`, `closed→[]`. Custom (unrecognised slug) statuses transition freely.

**Kanban drags route through services.** `apps/kanban/services.py::move_card` — when a dragged Ticket card moves to a column with a different `status` FK (and the board is NOT personal), calls `apps.tickets.services.change_ticket_status(...)` with the dragger as actor (full dual-write + ticket-feed broadcast + SLA pause). Personal boards never write back. Non-Ticket content (deals) does a plain `.save()`.

**Inbox Hub dual-write parity**: each Hub service fn writes an `ActivityLog` row (one of the 8 `EMAIL_*` actions or `STATUS_CHANGED`) AND broadcasts a `hub_email.*` LiveBus event on commit. No `TicketActivity`.

**Webhook service** (`apps/tickets/webhook_service.py`): `deliver_webhook` HMAC SHA-256 (`X-Webhook-Signature: sha256=<hex>` when secret set), 10s timeout, auto-disable at 10 failures. `fire_webhooks(tenant, event_type, data)` async via Celery. 8 EventType members.

## SLA + Business Hours (`apps/tickets/sla.py`)

Single breach-detection entry point `get_effective_elapsed_minutes()`: resolves per-tenant schedule via `BusinessHours` (JSON per-day + IANA tz) or legacy `TenantSettings` flat fields; skips `PublicHoliday` dates; subtracts total pause duration. Helpers: `elapsed_business_minutes()`, `add_business_minutes()` (365×24 iteration cap), `is_within_business_hours()`, `get_total_pause_minutes()`, `sla_deadline_utc()`. Falls back to **24/7 wall-clock** when no `BusinessHours` config. `initialize_sla(ticket)` seeds deadlines; `_check_first_response_breach` uses atomic UPDATE+WHERE.

> **Inbox Hub SLA is separate and simpler** — `_initialize_hub_sla` seeds **wall-clock** deadlines from `HubEmailSLA`; `check_hub_sla_breaches` (Beat 120s) flags + warns + auto-escalates. No business-hours math yet.

## Inbound / Outbound Email

### Inbound (`apps/inbound_email/`)
- **In-process SMTP server** via `aiosmtpd` (`run_smtp_server`, PM2 `kanzan-smtp`, default `0.0.0.0:2525`). `handle_RCPT` rejects **550** if no tenant resolves (anti-open-relay); 25MB cap (**552**); optional STARTTLS + LOGIN/PLAIN auth. Creates one InboundEmail per recipient.
- **IMAP poller** — shared Gmail-style mailbox; UID > watermark (not UNSEEN, because Gmail web-UI marks `\Seen`). Driven by `fetch_inbound_emails_task` (Beat 60s, `kanzan_email`). Disabled when `IMAP_HOST` blank. **Safety: never backfills** — aborts (returns 0) on UIDVALIDITY/UIDNEXT parse failure; watermark advanced per-message. **✅ Dedup is now PER-TENANT (Sprint 0):** `_ingest_one` resolves the tenant from the recipient BEFORE dedup and filters `(tenant, message_id)` — two tenants can legitimately receive the same Message-ID (was a global `message_id`-only check that silently dropped the second tenant's copy). Unresolvable recipient → `tenant=None` scope.
- **Tenant resolution** — 4 strategies in `resolve_tenant_from_address`: plus-addressing → slug-as-local-part → `TenantSettings.inbound_email_address` → `settings.IMAP_DEFAULT_TENANT_SLUG` last-resort.
- **Filters run BEFORE tenant resolution** (cheap, no DB): loop (`sender==DEFAULT_FROM_EMAIL`), 8 noreply prefixes, RFC 3834 Auto-Submitted/Precedence, subject patterns. `classify_email() → bounce/auto_reply/loop` (docstring's "legitimate" is never returned). Bounces write `BounceLog` + flip `Contact.email_bouncing=True`.
- **Threading** — `find_existing_ticket` 3-tier: In-Reply-To → References (reversed) → subject `[#N]` regex.
- **Processing pipeline** (`process_inbound_email_task`, max_retries=3, retry_delay=30s, acks_late): `select_for_update` (PENDING/PROCESSING proceed) → filters → tenant resolution → idempotency claim (`in:{tenant}:{mid}`) → find/create contact → find existing ticket OR (per the seam) `park_email_in_hub` if `inbox_hub_enabled` else `_create_ticket_from_email` (+ `_maybe_auto_assign`) → attach files → queue confirmation email via `on_commit` (if `auto_send_ticket_created_email`).
- **Agent inbox workflow** (`inbox_services.py`): `link_email_to_ticket`, `action_email` (open/assign/close), `ignore_email` — each dual-writes ActivityLog + (where applicable) TicketActivity. Exposed by `InboxViewSet` at `/inbound-email/inbox/...`.

### Outbound (`apps/tickets/email_service.py`)
- `send_ticket_email()` — single entry point. Skips undeliverable recipients (`.local`, RFC 2606); RFC Message-IDs; reply-to = tenant inbound address or `support+{slug}@{BASE_DOMAIN}`; thread headers from the ticket's recent message_ids. Persists an OUTBOUND `InboundEmail` (`out:` idempotency key) for threading. Dev: `filebased.EmailBackend` → `tmp/emails/`. Prod: SMTP. `_add_reply_to_ticket` deliberately does NOT fire `ticket_comment_created` (avoids emailing the customer into a loop) and reopens resolved/paused tickets on customer reply.

## Auto-Assign (Inbound Email → Agent — legacy, distinct from Inbox Hub)

`apps/agents/services.py::pick_email_agent(tenant)`:
1. Active member with **`role__hierarchy_level == 30`** (Agent/IT/HR — ⚠ raw role; excludes Admin/Manager/Team Lead/Viewer).
2. Not OFFLINE; agents with no `AgentAvailability` eligible (fail-open). ⚠ Checks only OFFLINE, not `is_assignable`/freshness (unlike the Hub engine).
3. Fewest open tickets (load balancing).
4. Tie-break by least-recently-assigned (NULLS FIRST).

`auto_assign_email_ticket(ticket)` — atomic save + `TicketAssignment` audit + best-effort `current_ticket_count` nudge. Gated by `TenantSettings.auto_assign_inbound_email_tickets`.

## VoIP

**Architecture:** Asterisk/FreePBX → ARI (REST + WebSocket Stasis). Django wraps ARI, exposes SIP creds to browser softphone (SIP.js over WSS), persists `CallLog`/`CallRecording`.

- **`ari_client.py`** — async `httpx` ARIClient. `ARIEventListener` connects to `ws(s)://host:port/ari/events?app=kanzan-voip&subscribeAll=true`, exponential reconnect.
- **`services.py`** — `check_call_limit` (the **only** `Plan.has_voip` enforcement), `increment_call_usage`, `process_ari_event` → `CallLog` updates + `_broadcast_call_event` → `voip_{tenant_id}`.
- **`consumers.py::CallEventConsumer`** (`ws/voip/events/`). ⚠ **No per-user/extension scoping** — every tenant member on the socket sees all call metadata (numbers, contact_id, ticket_id). Bare `close()` for rejections.
- **`run_ari_listener`** — one `ARIEventListener` per active tenant. **NOT in any PM2 config** — manual launch only.
- **Softphone** — `templates/includes/softphone.html` + `static/js/voip-softphone.js` using **SIP.js 0.21.2** (CDN, conditional on `voip_enabled` = `VoIPSettings.is_active`, **decoupled from `Plan.has_voip`** — UI can show while call placement is denied).

## API Architecture

### Authentication
- **API:** JWT (SimpleJWT) — 15min access, 7-day refresh, rotate + blacklist, HS256. **`APIKeyAuthentication`** (`Authorization: Api-Key kz_live_<slug6>_<secret>`). Returns `None` (fail-open) when header absent/different scheme; fails closed (401) on malformed/invalid/revoked/expired/cross-tenant key (timing-safe `compare_digest` of SHA-512).
- **`DEFAULT_AUTHENTICATION_CLASSES` order:** `JWTAuthentication` → `APIKeyAuthentication` → `SessionAuthentication`.
- **Frontend:** Session auth (Redis cached_db, host-only cookie). **SSO:** django-allauth (Google, Microsoft, OIDC), email-only login. **Global logout:** `User.auth_version` bump via `SessionVersionMiddleware`.

### `/api/v1/` Endpoint Map (23 router includes / 22 unique URLConfs — `inbound-email/` dual-mounts as `emails/`)

```
/tenants/      TenantViewSet (slug), TenantSettingsViewSet (singleton; per-field Manager allowlist incl. inbox_hub_* toggles)
/accounts/     AuthViewSet (throttle_scope="auth"), User, Role, Profile, Invitation, TenantMembership, UserGroup
/api-keys/     APIKeyViewSet (admin-only; mint/list/reveal-once/regenerate/revoke)
/tickets/      TicketViewSet (31 @action), TicketStatus, Queue, TicketCategory, SLAPolicy, EscalationRule, CannedResponse, Macro, SavedView, BusinessHours (singleton), PublicHoliday, TicketTemplate, Webhook, CSATSubmitView (public)
/contacts/     ContactViewSet (+context), Company, Account, ContactGroup
/billing/      PlanViewSet (AllowAny), SubscriptionViewSet (+cancel/reactivate), Invoice, Usage, checkout, webhook (CSRF-exempt, Stripe-signed)
/kanban/       BoardViewSet (+detail), Column, CardPositionViewSet (+move/reorder/add-ticket; actor+request aware)
/comments/     CommentViewSet, ActivityLogViewSet (read-only)
/messaging/    ConversationViewSet (+add/remove/leave/search-participants), MessageViewSet (+broadcast author-only action)
/notifications/  NotificationViewSet (+mark_read, unread_count, admin cleanup), NotificationPreferenceViewSet
/attachments/  AttachmentViewSet (multipart, true-MIME, cross-tenant validated)
/analytics/    DashboardView (APIView, ⚠ IsAuthenticated only), ReportDefinition, DashboardWidget, ExportJob, CalendarEvent
/agents/       AgentAvailabilityViewSet (grant/revoke_temp_role/assignable_roles excl admin), CustomAgentStatus
/custom-fields/  CustomFieldDefinition, CustomFieldValue (read-only)
/knowledge/    Category, ArticleViewSet (+submit_for_review/approve/reject/record_view/remove_file/preview_file/vote), KBSearchView
/notes/        QuickNoteViewSet
/inbound-email/  InboundEmailViewSet (read + create-ticket action; ?assigned=me/?internal=true/?mine=true) + InboxViewSet (link/action/ignore)
/emails/       alias mount of inbound_email.api_urls (namespace="emails_api")
/crm/          ActivityViewSet (+my-tasks), ReminderViewSet (+overdue/stats/complete/cancel/reschedule/bulk-action), PipelineForecastView
/nav/          BadgeCountView (7 categories incl. inbox_hub; effective_role; capped at 99)
/newsfeed/     NewsPostViewSet (+react/mark-read/mark-all-read/unread-count)
/voip/         VoIPSettings, Extension, CallLog (+active/stats), InitiateCall, CallHold, CallTransfer, CallHangup, SIPCredentials, CallRecordingDownload, CallQueue
/inbox-hub/    HubEmailViewSet (list/retrieve + 10 actions) + Department + RoutingRule + HubEmailSLA + QueueRouting
```

**Non-HTTP inbound channel:** `kanzan-smtp` PM2 process (`0.0.0.0:2525`). **Docs:** `/api/docs/` (Swagger), `/api/schema/` (OpenAPI 3.0). **DRF config:** `DEFAULT_PERMISSION_CLASSES=[IsAuthenticated]`; throttles `[ScopedRateThrottle, APIKeyRateThrottle]` (the latter opt-in-free — auto-engages when `request.auth` is an APIKey, buckets on `apikey-<pk>`); rates `auth 10/min, api_default 200/min, api_heavy 30/min, webhook 60/min, api_key 1000/hour`; PAGE_SIZE 50. Only `AuthViewSet` sets `throttle_scope="auth"`.

### Public / unauthenticated endpoints
- `POST /api/v1/tickets/csat/` (signed token), `GET /api/v1/billing/plans/` (`AllowAny`), `POST /api/v1/billing/webhook/` (HMAC, `@csrf_exempt`), `AuthViewSet.register/login/accept_invitation` (`AllowAny`, throttle `auth`).

### Frontend Routes (`apps/tenants/frontend_urls.py`) — 35 paths

```
/ login /register /logout /auth/handoff/ /verify-email/ /verify-email-sent/
/setup-company/ (login)  /workspaces/ (login)
/dashboard/ /tickets/ /tickets/new/ /tickets/<num>/ /contacts/ /contacts/create/ /contacts/<id>/
/calendar/ /kanban/ /messaging/ /analytics/ /knowledge/ /knowledge/<slug>/ /profile/ /api/quickstart/
/reminders/ /calls/ /inbound-email/  (all @_membership_required)
/users/ /billing/ /agents/ /groups/ /audit-log/  (@_role_required(20))
/emails/  (@_role_required(30) — agent-level outbound log)
/settings/  (@_membership_required + @ensure_csrf_cookie — API enforces admin write)
/inbox-hub/  (@_inbox_hub_access_required — login + active membership + group-gate → 403.html)
```

## WebSocket Endpoints (6 total — `main/asgi.py`)

Stack: `ProtocolTypeRouter({"http": …, "websocket": AllowedHostsOriginValidator(AuthMiddlewareStack(WebSocketTenantMiddleware(URLRouter(messaging + notification + ticket + voip + live))))})`. `WebSocketTenantMiddleware` resolves tenant from Host, sets `scope["tenant"]`; consumers verify membership.

1. **Chat:** `ws/messaging/{conversation_id}/` → `ChatConsumer`. Group `chat_{id}`. Limits 10KB/msg, 5 msg/s, 2s typing. Close 4001/4002/4003/4004. ⚠ `_create_message` hardcodes `"attachments": []` (attachments arrive via the REST `broadcast` action).
2. **Notifications:** `ws/notifications/` → `NotificationConsumer`. Group `notifications_{user_id}`. Bare `close()` for anon. **This is the rail the reminder-due popup rides.**
3. **Ticket Presence:** `ws/tickets/{ticket_id}/presence/` → `TicketPresenceConsumer`. Group `ticket_{id}_presence`. **Known gap:** docstring promises `presence_list` for newcomers — not implemented.
4. **Ticket Feed:** `ws/tickets/feed/` → `TicketListConsumer`. Group `ticket_feed_{tenant_id}`. Read-only.
5. **VoIP:** `ws/voip/events/` → `CallEventConsumer`. Group `voip_{tenant_id}`. ⚠ Tenant-wide call metadata, no per-user scoping. Bare `close()` for rejections.
6. **Live:** `ws/live/` → `LiveEventConsumer`. Group `live_tenant_{tenant_id}`. Stamps agent presence on connect + each `ping`.

> `apps/inbox_hub/routing.py` is the **RoutingEngine** (email→department classification), NOT a Channels route.

## Celery Tasks & Beat Schedule

### Queue Routing (`main/celery.py` — 8 globs incl. default)
```
apps.billing.tasks.*                              → kanzan_webhooks   (DORMANT — apps/billing/tasks.py does not exist)
apps.notifications.tasks.send_email_*             → kanzan_email
apps.notifications.tasks.send_notification_email  → kanzan_email
apps.inbound_email.tasks.*                        → kanzan_email
apps.tickets.tasks.send_ticket_*                  → kanzan_email
apps.api_keys.tasks.send_api_key_*                → kanzan_email
apps.voip.tasks.*                                 → kanzan_voip       (⚠ no PM2 worker subscribes)
*                                                 → kanzan_default
```
(No route for `apps.inbox_hub.tasks.*`, `apps.agents.tasks.*`, `apps.crm.tasks.*`, `apps.analytics.tasks.*`, `apps.knowledge.tasks.*` — all fall to `kanzan_default`, which IS consumed.) **`CELERY_BEAT_SCHEDULE` lives in `main/settings/base.py`, NOT `celery.py`.**

### Beat Schedule (12 tasks)

| Beat key | Task | Schedule |
|----------|------|----------|
| `check-sla-breaches` | `apps.tickets.tasks.check_sla_breaches` | 120s |
| `cleanup-old-notifications` | `apps.notifications.tasks.cleanup_old_notifications` | 86400s |
| `check-overdue-tickets` | `apps.tickets.tasks.check_overdue_tickets` | 900s |
| `calculate-lead-scores` | `apps.crm.tasks.calculate_lead_scores` | 86400s |
| `calculate-account-health-scores` | `apps.crm.tasks.calculate_account_health_scores` | 86400s |
| `kb-stale-alert` | `knowledge_base.alert_stale_articles` | crontab daily 08:00 (UTC) |
| `kb-gap-digest` | `knowledge_base.send_gap_digest` | crontab Mon 09:00 (UTC) |
| `cleanup-stale-calls` | `apps.voip.tasks.cleanup_stale_calls` | 3600s (⚠ piles up — kanzan_voip unconsumed) |
| `fetch-inbound-emails` | `apps.inbound_email.tasks.fetch_inbound_emails_task` | 60s |
| `reap-stale-presence` | `apps.agents.tasks.reap_stale_presence` | 60s |
| `check-hub-sla-breaches` | `apps.inbox_hub.tasks.check_hub_sla_breaches` | 120s |
| **`fire-due-reminders`** (Feature A, uncommitted) | `apps.crm.tasks.fire_due_reminders` | 30s |

Celery Beat uses the **built-in shelve scheduler** (`celerybeat-schedule`). `django-celery-beat` removed (Django 6 incompat); only `django_celery_results` is installed. **`apps.crm.tasks.check_overdue_reminders` and `apps.tickets.tasks.check_sla_breach_warnings` exist but are NOT in Beat.**

### Task Inventory (27 tasks across 10 modules)

- **tickets** (10): `check_sla_breaches`, `check_overdue_tickets`, `send_ticket_reply_email_task`, `send_ticket_created_email_task`, `send_ticket_email_task`, `auto_close_ticket`, `send_csat_survey_email`, `deliver_webhook_task`, `check_sla_breach_warnings` (not in Beat), `propagate_sla_policy_change_task`
- **crm** (4): `calculate_lead_scores`, `calculate_account_health_scores`, `check_overdue_reminders` (not in Beat — dead), **`fire_due_reminders`** (Beat 30s, Feature A)
- **voip** (3): `process_call_recording`, `cleanup_stale_calls`, `sync_call_state` (queue `kanzan_voip`, unconsumed)
- **inbound_email** (2): `fetch_inbound_emails_task`, `process_inbound_email_task`
- **knowledge** (2): `alert_stale_articles`, `send_gap_digest` (registered `knowledge_base.*`)
- **notifications** (2): `send_notification_email`, `cleanup_old_notifications`
- **agents** (1): `reap_stale_presence`
- **analytics** (1): `process_export_job`
- **api_keys** (1): `send_api_key_created_email_task`
- **inbox_hub** (1): `check_hub_sla_breaches`

## PM2 Processes — 5 prod / 4 dev

### `ecosystem.config.js` (prod, venv `.venv/`)

| Name | Purpose |
|------|---------|
| `kanzan-django` | `gunicorn main.asgi:application -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8001 --timeout 120` (2GB) |
| `kanzan-celery-worker` | `celery -A main worker -Q kanzan_default,kanzan_email,kanzan_webhooks -c 4 --max-tasks-per-child=200` (2GB) |
| `kanzan-celery-beat` | `celery -A main beat -l info` |
| `kanzan-flower` | `celery -A main flower --port=5556 --basic_auth=$KANZAN_FLOWER_AUTH` |
| `kanzan-smtp` | `manage.py run_smtp_server` (2525) |

> **⚠ Prod & dev worker `-Q` = `kanzan_default,kanzan_email,kanzan_webhooks`** — `kanzan_voip` is in `celery.py` routes but **no worker subscribes** (the 3 voip tasks incl. Beat `cleanup-stale-calls` accumulate). `run_ari_listener` is **not in PM2**. **Makefile `stop`/`restart` omit `kanzan-smtp`** (so a `make stop` leaves prod SMTP running). The prod config header comment is **stale** (claims a Unix-socket/Nginx bind; actual `args` bind `0.0.0.0:8001`).

### `ecosystem.dev.config.js` (dev, venv `env/` → `.venv/`) — 4 processes
- `kanzan-django` runs `manage.py runserver 0.0.0.0:8001`. `kanzan-celery-worker` `-c 2`, **watch** on `apps/*/{tasks,services}.py` + `main/celery.py`. `kanzan-celery-beat`, `kanzan-flower`. **No `kanzan-smtp`** (dev inbound relies on IMAP polling only).

## Frontend Architecture

### JavaScript (`static/js/`, 14 modules, 5,722 LOC — vanilla, no React/Vue)

| Module | LOC | Role |
|--------|----:|------|
| `inbox-hub.js` | **1,378** | Triage COCKPIT (`?v=10`): 5 lenses (SLA self-hides at 0), 4-count fetch, customer-context card via `GET /hub-emails/{id}/context/`, **attachment thumbnails/download**, SLA badge + open-ticket nudge, **full `#ihConvertPanel` convert offcanvas** (TipTap + tags + flatpickr) / assign / dismiss + floating Assign menu, J/K/C/A/X/Esc, 7 LiveBus subs (400ms). NO claim/escalate/transition/note UI |
| `app.js` | **1,139** | Global init: alerts, sidebar collapse/density, notification WS, Toast, `Kanzan.formatDate/…`, `initSidebarBadges` (7 categories), `initLiveStatusPill()`, `initSidebarUserLive()`, **`ReminderAlerts` IIFE** (Feature A). Bell flyout 3s auto-fade |
| `voip-softphone.js` | 710 | SIP.js 0.21.2 + `CallEventConsumer`. Dial/DTMF/mute/hold/transfer/hangup (naive 5s fixed reconnect) |
| `custom-select.js` | 371 | `KanzenSelect` portal-rendered styled selects (searchable >8) |
| `command-palette.js` | 337 | Cmd+K modal (⚠ "New Contact" → dead `/contacts/new/`) |
| `keyboard-shortcuts.js` | 318 | Global hotkeys; injects runtime `<style>` using `var(--crm-primary)` |
| `ticket-feed.js` | 248 | `ws/tickets/feed/` → republishes into LiveBus; banner + row pulse (ticket_assigned Toast omitted) |
| `agent-availability.js` | 244 | Status toggle + `subscribePresence()` reflects server `agent.presence` (no heartbeats) |
| `notes-panel.js` | 238 | Quick notes CRUD (6 colors, pinning, 600ms autosave) |
| `live-connection.js` | 206 | Single shared `ws/live/`, 25s heartbeat / 8s pong, backoff 1s→30s ±20% jitter (infinite) |
| `rich-editor.js` | 191 | TipTap wrapper (page-specific; textarea fallback) |
| `live-bus.js` | 175 | Global pub/sub `window.LiveBus` (BroadcastChannel cross-tab) |
| `api.js` | 90 | Central API client (CSRF cookie + meta fallback, same-origin, JSON + multipart) |
| `theme.js` | 77 | light/dark/system (default dark). Loaded SYNCHRONOUSLY in `<head>` |

> ⚠ Multiple independent notification-WS backoffs with different caps: `app.js initNotifications` (`/ws/notifications/`, max **10**) vs `live-connection.js` (`/ws/live/`, **infinite**) vs `ticket-feed.js` (max 10) vs `voip-softphone.js` (5s fixed). Only `inbox-hub.js` is cache-busted (`?v=10`).

### CSS & Theming

- **`static/css/custom-v15.css`** — **25,149 LOC**, "Design System v9.0 Crimson Black" (the only loaded project CSS). Token scales: `--crm-radius-{xs:4,sm:8,md:6,lg:14,xl:16,pill:9999}` (⚠ `md`<`sm` quirk), `--crm-weight-{normal:400,medium:500,semibold:600,bold:700}`, `--crm-z-{base:1,sticky:10,dropdown:1000,modal-backdrop:1040,modal:1050,popover:1060,tooltip:1070,flyout:1080,overlay:1085,toast:1090}`. Inbox Hub cockpit section (`body.ih-page` grid, `.ih-context-*`, `.ih-sla-badge--*`, `.ih-menu*`, **`.ih-convert-*` offcanvas + attachment chips** + reduced-motion guard). reminder-due-modal block (`.reminder-due-*`, `@keyframes reminderDuePulse`, reduced-motion guard). All token-only (zero hex → theme-check green).
- **`static/css/custom.css`** — 20,431 LOC committed snapshot, NOT loaded, allowlisted in theme check.
- **No hex literals in rule bodies.** `make theme-check` enforces against `scripts/.theme_baseline.json` (**baseline 147 hex / 11 files** — custom-v15.css 81, landing_crm.html 21, dashboard.html 13, landing.html 15, contacts/list 5, kanban/board 4, verify_email_sent 3, tickets/detail 2, login 1, reminders/list 1, tickets/list 1). Allowlist: `colors.py`, `check_theme.py`, `settings/tenant.html`, `theme.js`, `custom.css`. Scans inside `<script>` blocks (since `22f284a`). **PASSES** (runtime "146 tracked" — 1 masked literal, cosmetic). ⚠ `base.html` toast container has a hardcoded inline `z-index:1090` (not tokenized; invisible to the hex-only check).

### Templates (48 .html files)

- `templates/base.html` (292 lines) — palette `<style>`, toast container, quick-notes panel, **NEW `#reminderDueModal`** (static backdrop), softphone (conditional), DOMPurify 3.2.4 + SIP.js 0.21.2 CDN (conditional) + Flatpickr loader + synchronous `kanzan_sidebar_collapsed` pre-paint. Default theme: dark. 5 stylesheets (only custom-v15.css is project CSS).
- `templates/includes/` (6 files): `navbar.html` (189), `sidebar.html` (162 — Inbox Hub FIRST in "Inbox" section gated `{% if can_access_inbox_hub %}`), `softphone.html` (169, conditional), `messages.html` (21), `page_back_button.html` (included by **18**), `kb_sidebar_widget.html` (158 — **ORPHAN, safe-deletion candidate**).
- `templates/pages/` — **18 subfolders + 8 root files** (403, api_quickstart, calendar, dashboard, landing, login, profile, register).
- Email templates: 6 single files under `{auth,knowledge,notifications,tickets}/email/`. `landing/landing_crm.html` (1,393 LOC) — standalone marketing page.

### Notable page templates

- **`profile.html`** (444) — Profile v2, `.pf2-*` namespace, inline-edit; role badge = 1 of 4 variants (Team Lead/IT/HR collapse to `--agent`).
- **`reminders/list.html`** (3,638) — split-pane workspace; **natural-language quick-add parser**; 5-col stat grid; body-portaled Filters popover. LiveBus 5 verbs + `live.reconnected` **+ `reminder.due`** (Feature A), debounced 500ms.
- **`emails/list.html`** (1,196) — "Assigned to me" stat tab; dual-source load; `#createTicketModal` override form (Feature B) → `/inbound-email/{id}/create-ticket/`, idempotent-400 handled, dual-modal-glitch guard; inline attachment thumbnails + download.
- **`audit_log/list.html`** (2,273) — two tabs; Insights redesign; export mirrors live filters + walks all pages; `dedupTimeline()`.
- **`tickets/list.html`** (1,470) — **dynamic per-status stat tabs** (`buildStatusTabs` from `/tickets/ticket-statuses/`; static tabs All + Urgent).
- **`tickets/detail.html`** (3,762) — Delete-Ticket REMOVED. **Macro dropdown HTML stripped but ~44 lines of macro JS remain as dead-but-present no-ops.** TipTap via importmap.
- **`groups/list.html`** (734) — smart member picker (excludes users in other groups; server response is a **flat string in `detail`**, not a `{conflicts:[…]}` array).
- **`settings/tenant.html`** (5,116) — searchable hub; 2 Inbox Hub toggles; API-keys pane; color-picker (theme-check-allowlisted).
- **`kanban/board.html`** (1,999) — SortableJS column DnD; cross-status drags route through tickets service.

### Context Processor (`apps/tenants/context_processors.py`)

Injects: `tenant`, `membership` (cached on `request._cached_tenant_membership`), `user_role` (= `effective_role`), `is_admin`/`is_admin_or_manager`/`is_agent_or_above`, **`can_access_inbox_hub`**, `voip_enabled` (= `VoIPSettings.is_active`), `tenant_palette` (~21-key), `BASE_URL`.

## Middleware Stack (14 layers)

1. SecurityMiddleware 2. WhiteNoiseMiddleware 3. SessionMiddleware 4. CorsMiddleware 5. CommonMiddleware 6. CsrfViewMiddleware 7. AuthenticationMiddleware 8. AccountMiddleware (allauth) 9. **SessionVersionMiddleware** (global logout via `auth_version`) 10. **TenantMiddleware** (tenant resolution; `/admin/` dedicated branch — after auth so `request.user` is available) 11. **SubscriptionMiddleware** (402 when neither active nor in grace) 12. **RateLimitHeadersMiddleware** (`apps.api_keys.middleware` — read-side `X-RateLimit-*`) 13. MessageMiddleware 14. XFrameOptionsMiddleware

## Billing Plans (⚠ FROM THE SEEDER — `seed_plans.py`)

| Plan | $/mo | Users | Contacts | Tickets/mo | Storage | Custom Fields | API | Realtime | Custom Roles | SSO | SLA | Audit days |
|------|---|-------|----------|-----------|---------|---|-----|-----|-----|-----|-----|-----|
| Free | 0 | 3 | 500 | 100 | 1GB | 5 | No | No | No | No | No | 30 |
| Pro | 29 | 25 | 10K | 5K | 25GB | 50 | Yes | Yes | Yes | No | Yes | 365 |
| Enterprise | 99 | ∞ | ∞ | ∞ | ∞ | ∞ | Yes | Yes | Yes | Yes | Yes | ∞ |

> **✅ VoIP entitlement NOW SEEDED (Sprint 0 — was the `billing-1` Critical).** `seed_plans.py` sets per plan: **Free** `has_voip=False / has_call_recording=False / max_calls_per_month=0`; **Pro** `True / True / 1000`; **Enterprise** `True / True / None` (unlimited). Migration `billing/0003_backfill_voip_plan_flags` backfills existing `Plan` rows by tier. So `voip/services.py::check_call_limit` (the **only** `Plan.has_voip` enforcement) now permits calls on Pro/Enterprise — the old "denies every seeded plan" footgun is gone. (Softphone *UI* visibility remains gated by `VoIPSettings.is_active`, still decoupled from `Plan.has_voip`. The dead `require_feature("voip")` decorator is unchanged — still 100% unused.)

## Management Commands (8 total)

```bash
python manage.py provision_tenant --name "Acme" --slug acme [--domain crm.acme.com]
python manage.py seed_plans                                    # Free/Pro/Enterprise (idempotent — ✅ now sets VoIP flags)
python manage.py setup_queues --tenant-slug demo               # default queues
python manage.py setup_ticket_statuses --tenant-slug demo      # 5 default statuses
python manage.py backfill_sla_audit [--tenant-slug] [--dry-run]
python manage.py seed_inbox_hub_defaults [--tenant-slug <slug> | --all-tenants]  # General dept + memberships only
python manage.py run_smtp_server                               # kanzan-smtp PM2 process
python manage.py run_ari_listener                              # VoIP Stasis event loop (NOT in PM2)
```

## Environment Variables

- **`.env.example` (16 keys):** `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DATABASE_URL`, `REDIS_URL`, `BASE_DOMAIN`, `BASE_SCHEME`, `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`, `JWT_SECRET_KEY`, `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `KANZAN_FLOWER_AUTH`.
- **base.py reads 37 env keys; ~22 are read-but-undocumented:** `BASE_PORT`, `DEFAULT_FROM_EMAIL`, `EMAIL_TIMEOUT`, `EMAIL_USE_SSL`, `INBOUND_EMAIL_WEBHOOK_SECRET`, 7×`IMAP_*` (incl. `IMAP_DEFAULT_TENANT_SLUG`), 7×`SMTP_SERVER_*`, `AGENT_PRESENCE_TTL_SECONDS`, `AGENT_PRESENCE_AUTO_ONLINE`, `HUB_SLA_WARNING_MINUTES`.
- **`KANZAN_FLOWER_AUTH` is documented but NOT read by `base.py`** — consumed only by `ecosystem.config.js`.

## Testing

- **Framework:** pytest + pytest-django. **66 modules** (59 root + 7 app-level). `pytest.ini`: 3 lines (`DJANGO_SETTINGS_MODULE=main.settings`, `pythonpath=.`, **no `asyncio_mode`** → defaults `strict`; `requirements/dev.txt` now ships `pytest-asyncio` but pytest.ini doesn't set the mode).
- **Fixtures (`conftest.py`, 343 LOC):** **16 factories + 20 fixtures** (3 autouse: `celery_eager`, `free_plan`, `clear_tenant_context`). `RoleFactory` has 4 traits (admin/manager/agent/viewer); team-lead/it/hr fetched via `Role.unscoped.get(slug=...)`. `ReminderFactory` sets `priority="medium"` + `scheduled_at=now()` (eligible to fire immediately in `fire_due_reminders` tests).
- **on_commit tests** wrap POSTs in `django_capture_on_commit_callbacks(execute=True)`.
- **✅ FULL SUITE GREEN — verified run 2026-06-16:** `python -m pytest -q` = **894 passed / 0 failed / 22 skipped / 1 xfailed** (~185s on SQLite; up from 864 on 06-15 as Feature B / inbound-attachments / Hub-access-refactor added ~30 tests). This long ago RESOLVED the QA audit's **18 failed / 834 passed** snapshot (outbound-email RFC-2606 regressions re-pointed `@example.com` → `@clientmail.com`; 2 stale comment→chat badge tests; 1 comment-visibility test). The 22 skips are env-gated (Postgres-only FTS etc.); the 1 xfail is expected. Sprint-0 + new tests pass: `test_messaging_tenant_isolation`, `test_attachment_authz`, `test_billing_voip_and_webhook`, `test_imap_poller_safety::TestCrossTenantDedup`, plus `TestHubConvertOverrides` / `TestHubEmailAttachments` / `TestInboundEmailAttachments` (in existing modules). `makemigrations --check` clean; `make theme-check` green; `ruff check .` = 197 lint issues (non-blocking).
- ⚠ `make test-fast` uses `--timeout=30` but `pytest-timeout` is STILL NOT in `dev.txt` (`make test-fast` errors without it).

## Documentation

- `/CLAUDE.md` (this file) — day-to-day source of truth.
- `/docs/README.md` — index; defers to `/CLAUDE.md`.
- `/docs/architecture.md` — long-form (Version 1.0, **2026-02-06**; STALE design rationale only).
- `/docs/ui-consistency-audit.md` (2026-05-22) — most recommendations shipped; figures outdated.
- `/docs/reference/{codebase-inventory,api-surface,frontend-surface,infra-surface}.md` — Verified 2026-05-22 @ `ea87bb2`; **STALE** (predate inbox_hub, the access refactor, presence layer, both features, Sprint 0). CLAUDE.md wins on any disagreement. Structure is fine; regenerate against current HEAD when convenient.
- **`/docs/qa-audit-2026-06-14/` (11 files)** — the end-to-end QA/security/perf audit that drove this branch. `00-EXECUTIVE-SUMMARY` (38/100), `01-QA-REPORT`, `02-BUG-REPORT`, `03-SECURITY-ASSESSMENT`, `04-UIUX-AUDIT`, `05-PERFORMANCE-AUDIT`, **`06-REMEDIATION-PLAN`** (Sprint 0–3 fix plan — Sprint 0 is what this branch implements), + 4 `_digest_*` raw appendices. Still-current for Sprint 1–3 backlog items.
- `/scripts/check_theme.py` + `.theme_baseline.json` — regression guard (baseline 147 / 11 files).
- `README.md` — minimal stub (`# Kanzen`).

## Common Pitfalls & Fixes Applied

1. `ACCOUNT_LOGIN_METHODS = {"email"}` (a set); all apps need `migrations/__init__.py`; DRF ≥3.16 (Django 6); `django-celery-beat` removed; `django.contrib.postgres` installed (KB FTS).
2. **`daphne` + `jazzmin` ARE in INSTALLED_APPS** (admin is jazzmin "darkly", superuser-locked via `SuperuserOnlyAdminSite` class-swap; `main/admin.py` registers 0 models).
3. **Working tree DIRTY: THREE uncommitted layers** — Sprint 0 QA fixes (this branch) + Feature A (reminder-due popup) + Feature B (create-ticket-overrides) **+ Hub access refactor + inbound-email attachments** (these three landed together 06-15→16). Counts: migrations **119**, models **91**, test modules **66**, beat 12, tasks 27, NotificationType 21, ActivityLog 34, TicketActivity 27, custom-v15.css **25,149**, app.js **1,139**/JS **5,722**, pytest **894 pass**. **CI now exists** (`.github/workflows/ci.yml`). See **§Sprint 0 Hardening**.
4. **Viewer IS seeded — 7 system roles** (`ROLE_DEFINITIONS` has 6, Viewer permission-less). **`apps.nav` is NOT installed** (21 `apps.*`).
5. **91 model classes** across 21 apps. **5 polymorphic GenericFK models:** Attachment, Comment, ActivityLog, CustomFieldValue, CardPosition. **Notification is NOT polymorphic.**
6. **`/admin/` NOT in `EXEMPT_PATH_PREFIXES` (17 entries)** — dedicated branch sets context even to None. `IsTenantMember` returns True when no tenant. **`/inbound/email/` exempt entry is dead** (no URLConf).
7. **DRF auth order JWT → APIKey → Session.** API keys `kz_live_<slug6>_<token_urlsafe(32)>` — SHA-512; shown once. `APIKeyRateThrottle` opt-in-free.
8. **Always use `effective_role`** — BUT mixed `role` vs `effective_role` drift remains in `tickets/views.py` list, `analytics`, `kanban` serializer, `pick_email_agent` (raw role), and Hub `_candidate_user_ids` no-dept fallback. Temp-promoted agents are inconsistently scoped.
9. **⚠ Inbox Hub access is DEPARTMENT-SCOPED** (`inbox_hub/access.py`; the old `UserGroup` gate is DELETED) — Admin/Manager (≤20) always; Viewer (>30) never; agent-tier iff (in active dept) OR (tenant has no active depts, fall-open) OR (has assigned mail, black-hole valve). Row-scope (`hub_rows_q`/`agent_can_see_hub_email`): ≤20 all rows; agent-tier → `assignee=me OR (state=NEW AND department∈my-depts/NULL)`. Rollout: dept-having tenants must make triagers Department members or they 403.
10. **⚠ Agent ticket visibility TIGHTENED** (`tickets/access.py`) — `assignee=me OR (created_by=me AND assignee IS NULL)`. Self-created ticket handed off LEAVES the creator's view. Shared across 5 surfaces.
11. **Agent email-inbox handoff** — manual Hub assign/reassign/claim stamps `InboundEmail.assignee` (+PENDING/unread); **auto-assign does NOT**. New `create_ticket` action on `InboundEmailViewSet` (role ≤30).
12. **Inbox Hub frontend is a TRIAGE COCKPIT** (`?v=10`) — 5 lenses + customer-context card + attachment thumbnails + full `#ihConvertPanel` convert offcanvas. Backend `claim/escalate/transition/note` exist but UI never calls them. **No `reply` action** despite the seeded codename.
13. **Inbox Hub presence is heartbeat-driven** — `/ws/live/` 25s ping stamps `last_seen`; `reap_stale_presence` (60s) ages stale ONLINE→AWAY at 90s TTL; `is_assignable` is the single auto-assign gate (don't confuse with looser `is_available`). Auto-assign gated by `inbox_hub_auto_assign` (default True).
14. **`first_responded_at` read-but-never-written** → Hub response-breach always fires (+auto-escalates). **`escalate_hub_email` bumps `escalation_count` even on illegal transition.** `HubEmailAssignment.Reason.ESCALATION` never emitted. **`InboundEmail.Status.PARKED_IN_HUB` write-dead.**
15. **`apps/inbox_hub/routing.py` is the RoutingEngine**, NOT a Channels route. `apps/inbox_hub/` has NO `signals.py`/`ready()`.
16. **✅ Feature B reachability gap CLOSED** — both the Emails-page `create_ticket` AND the Hub `convert_to_ticket` now call the shared `apps/inbound_email/ticket_overrides.py::build_ticket_overrides`, so the full 9-field override set (subject/description/category/due_date/tags + queue/status/assignee/priority) reaches both. `ConvertToTicketSerializer` is schema-only. The cockpit drives it via `#ihConvertPanel`. **NEW: inbound-email attachments** — `apps/inbound_email/attachments.py` + authed `attachment/` action on both `InboundEmailViewSet` and `HubEmailViewSet` (inline raster / forced download + `nosniff`).
17. **`process_inbound_email` variable-shadowing** — local `settings` rebinds module-level `django.conf.settings`. **IMAP "never backfill"** safety. Filters run BEFORE tenant resolution.
18. **✅ BILLING VoIP flags NOW SEEDED (Sprint 0)** — Free off / Pro `True/True/1000` / Enterprise `True/True/None`; mig `billing/0003` backfills. `check_call_limit` permits Pro/Enterprise. **`require_feature` decorator is still 100% dead.** `voip_enabled` (softphone UI) gated by `VoIPSettings.is_active`, still decoupled from `Plan.has_voip`. **Also fixed: re-subscribe IntegrityError** (webhook repoints the OneToOne row).
19. **VoIP runtime is manual** — `kanzan_voip` queue unconsumed; `run_ari_listener` not in PM2; `cleanup-stale-calls` Beat messages pile up. `CallEventConsumer` is tenant-wide (no per-user scoping).
20. **`check_overdue_reminders` is dead** (not in Beat; docstring honestly corrected). `check_sla_breach_warnings` also unscheduled. **`fire_due_reminders` (Feature A) IS live @30s** — supersedes the due-alert gap, NOT the overdue nag.
21. **Company custom fields never synced** (no `Company.post_save` in `custom_fields/signals.py`). `Account.health_score`/`Contact.lead_score` clamp only in `clean()` (never auto-called); recalc tasks use `.update()` → invisible to the live layer.
22. **ContactEvent emits ZERO live events** — `log_contact_event` writes `last_activity_at` via `.update()` (no `post_save`); the `contacts/signals.py` docstring claiming a `contact.updated` is false. Internal comments leak on the live channel. `TicketPresenceConsumer presence_list` unimplemented.
23. **Kanban drags → ticket service** for cross-status drags (non-personal boards). Orphan-card cleanup via `Ticket.post_delete` (hard-delete only). Pipeline-stage→column sync matches by **name** (rename breaks it).
24. **`Ticket.save()` auto-fills `company`** from linked Contact (never overwrites explicit). **`Article.save()` resolves tenant from context** + slug-collision loop (FTS is a dev no-op on SQLite). `TicketActivity` inner enum is `Event`, NOT `EventType`.
25. **ActivityLog 34 actions / NotificationType 21 (+REMINDER_DUE) / TicketActivity 27 Events.** **HubEmail 9 states / 4 priorities** (no medium; Reminder priority HAS medium).
26. **`Queue.department` FK opt-in/nullable.** **`Board.is_personal`** private to creator. **`Conversation.source_group`** FK dedupes group convs. **`UserGroup`** "one user per group" enforced (flat-string `detail`); gates the Hub.
27. **No hex literals in CSS/JS/template rule bodies** — `make theme-check` enforces (baseline 147/11). ⚠ `base.html` toast `z-index:1090` is a hardcoded magic number (invisible to the hex-only check).
28. **`tickets/detail.html` Delete-Ticket REMOVED**; macro JS dead no-ops. **`tickets/list.html` stat tabs dynamic.** **Reminders v2 NL quick-add.** **Profile v2 4 role badges.**
29. **`kb_sidebar_widget.html` is an orphan** (safe-delete). **`page_back_button.html` included by 18.** **`KBRevision`/`KBTicketLink` are dead-write models.** **`command-palette.js` "New Contact" → dead `/contacts/new/`.**
30. **✅ CI NOW EXISTS (Sprint 0)** — `.github/workflows/ci.yml` (ruff non-blocking + migrate-check + theme-check + pytest on PG16+Redis7, push-to-`main`/PR); `make check` remains the local gate. **`requirements.txt` byte-identical to `requirements/base.txt`.** **Logs not rotated** (~95MB). **`make logs-django` errors** (`.PHONY`, no body). **`make stop`/`restart` skip `kanzan-smtp`.** **`pytest-timeout` still missing from `dev.txt`** (but `pytest-asyncio` now present). **`analytics.DashboardView` under-permissioned** (IsAuthenticated only — Sprint 1 item).
31. **✅ Sprint-0 cross-tenant/security fixes:** DEBUG default→False; messaging user_ids tenant-scoped; attachment object-level authz (raw `/media/` still open); IMAP dedup per-`(tenant,message_id)`. **`makemigrations --check` clean; 119 migrations** (3 untracked: `billing/0003`, `crm/0005`, `notifications/0006`). Latest per heavy app: accounts 0012, agents 0007, inbound_email 0010, tenants 0010, tickets 0027, comments 0010, billing 0003, crm 0005, notifications 0006, inbox_hub 0001.
