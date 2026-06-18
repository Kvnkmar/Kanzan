# Kanzen — Security Assessment
**Date:** 2026-06-14 · **HEAD:** `9575577` + 2 uncommitted features

## Executive view
Kanzen's security **foundation is good**: contextvars-based tenant binding with a **fail-closed** `TenantAwareManager` (`.none()` when no tenant), timing-safe SHA-512 API-key comparison, short-lived rotating JWTs, host-only session cookies with signed cross-host handoff, superuser-locked admin, true-MIME upload validation, and HMAC-verified Stripe/webhook signatures. The DRF auth order (JWT → APIKey → Session) is correct and API keys fail-closed on tamper.

**However**, several confirmed issues break the multi-tenant security promise or expose data **within** a tenant across role boundaries. The single most dangerous issue is a **configuration footgun that can silently run production in DEBUG mode**.

> 3 of the originally-reported Critical security items were **refuted on verification** and excluded: the `.env` file is **not** committed to git (gitignored; verified via `git ls-files`/`git log`), and two "cross-tenant" claims about `InboundEmail` creation windows were over-rated. They are noted as residual hygiene items, not active vulnerabilities.

## OWASP Top 10 (2021) mapping
| OWASP | Status | Findings |
|---|---|---|
| A01 Broken Access Control | ❌ **Multiple** | attachment object-level authz (`attachments-1/2/3`), `DashboardView` RBAC bypass (`authn-2`), `role`↔`effective_role` drift (`authz-rbac-2`, `tickets-core-7`), TicketPresence visibility (`websockets-3`), VoIP broadcast scope (`websockets-1`), `visible_to_roles` unenforced (`custom-fields-agents-3`), cross-tenant `assignee` (`data-model-integrity-1`) |
| A02 Cryptographic Failures | 🟢 | API keys SHA-512 + `compare_digest`; JWT HS256; VoIP creds encrypted. (Reserved-domain skip prevents bounce leakage.) |
| A03 Injection | 🟢 | ORM throughout; DOMPurify on rich content; RoutingRule regex fails closed. No raw SQL injection found. |
| A04 Insecure Design | ⚠️ | `InboundEmail` not tenant-scoped (`tenant-isolation-3`); validators in `clean()` never invoked; claim-first-outside-transaction patterns |
| A05 Security Misconfiguration | ❌ **Critical** | `DJANGO_DEBUG` split-default (`settings-secrets-1`); `/media/` served without auth (`attachments-3`); unrotated logs |
| A06 Vulnerable Components | 🟢 (not deep-scanned) | Django 6.0.2 / DRF 3.16+ current; recommend `pip-audit`/Dependabot |
| A07 Auth Failures | ⚠️ | Invitation token reuse on race (`authn-3`); auth throttle present (`auth 10/min`) |
| A08 Data Integrity Failures | ⚠️ | Stripe `OneToOne` re-subscribe break (`billing-2`); signal-bypassing `.update()` writes |
| A09 Logging/Monitoring | ⚠️ | Good app logging, but no rotation (95 MB) and swallowed exceptions in several tasks |
| A10 SSRF | 🟢 (review) | ARI/webhook URLs are operator-configured; recommend egress allowlist for outbound webhooks |

---

## 🔴 Critical security findings

### SEC-C1 · `DJANGO_DEBUG` split-default can run production in DEBUG `[settings-secrets-1]`
- **Location:** `main/settings/__init__.py:9` (`default=True`) vs `main/settings/base.py:17` (`default=False`); `main/settings/dev.py` (`DEBUG=True`, `ALLOWED_HOSTS=["*"]`).
- **Impact:** A prod deploy that forgets `DJANGO_DEBUG=False` **silently loads `dev.py`** → DEBUG tracebacks (SQL, settings) exposed, `ALLOWED_HOSTS=["*"]` (Host-header/cache-poisoning), `SESSION/CSRF_COOKIE_SECURE=False`, no HSTS.
- **Verified:** by hand (lines read).
- **Fix:** Make `__init__.py` default `False` (fail-safe to prod), **or** raise on unset in non-dev. Add a startup assertion that `DEBUG is False` when `BASE_SCHEME=https`.

### SEC-C2 · Cross-tenant user enumeration & conversation injection `[messaging-1/2/3]`
- **Location:** `apps/messaging/mentions.py:74`, `serializers.py:337-346` (DM), `:482-503` (group), also `views.py:398`, `consumers.py:317`.
- **Impact:** Mentions resolve users via `User.objects.filter(id__in=user_ids)` (global). DM/group creation accept arbitrary user UUIDs with no `TenantMembership` check → a user can enumerate/mention/DM users in **other tenants** and pull cross-tenant participants into a conversation.
- **Verified:** by hand (`mentions.py:74`).
- **Fix:** Filter all three sites by `tenantmembership__tenant=tenant, is_active=True`; reject non-member `user_ids` in DM/group validation.

### SEC-C3 · Attachment authorization gaps (upload / retrieve / download) `[attachments-2, attachments-1, attachments-3]`
- **Location:** `apps/attachments/serializers.py:106-124`, `views.py:164`, `main/urls.py:46-47`.
- **Impact:** Within a tenant, **any** member can attach files to / read attachments of objects they cannot otherwise see (e.g., other agents' tickets, internal comments). Object-level `has_object_permission` returns `True` unconditionally; uploads validate tenant but not object access; `/media/` files have no auth gate.
- **Verified:** by hand (upload serializer validates tenant only).
- **Fix:** Add an object-level permission (reuse `agent_visible_tickets_q`, comment/message access) enforced in both the upload serializer and `check_object_permissions`; serve files through an authz'd download view (`FileResponse` / `X-Accel-Redirect`).

*(Note: `tenant-isolation-3`/`inbound-email-1` cross-tenant email handling is a Critical with a security dimension; full detail in `02-BUG-REPORT.md` BUG-C3.)*

---

## 🟠 High security findings
| ID | Title | Location | Fix |
|---|---|---|---|
| `authz-rbac-2` | `role.hierarchy_level` used instead of `effective_role` in 20+ sites — temp-role grants ignored by list/analytics/kanban/auto-assign querysets | `apps/agents/services.py:226`, `apps/analytics/services.py:52`, `apps/kanban/serializers.py:188`, `apps/tickets/views.py:576`, +16 | Replace with `effective_role`; add a lint guard |
| `tickets-core-7` | `bulk_action` delete uses raw `role` → temp-Manager denied | `apps/tickets/views.py:1376-1391` | Use `effective_role.hierarchy_level` |
| `data-model-integrity-1` / `tickets-core-1/2` | `Ticket.clean()` assignee-membership never invoked; `assignee` FK not tenant-scoped → cross-tenant assignee | `apps/tickets/models.py:579-589`, `serializers.py:516+` | `validate_assignee` (tenant-member check) + `full_clean()` in create/update |
| `authn-2` | `DashboardView` has `IsAuthenticated` only — no `HasTenantPermission` (RBAC bypass) | `apps/analytics/views.py:196` | Add `HasTenantPermission` + `analytics.view` codename |
| `authn-3` | Invitation tokens reusable under race (only `is_accepted` checked) | `apps/accounts/views.py:686-758` | Atomic `consumed_at` + email match |
| `websockets-1` | VoIP `CallEventConsumer` broadcasts full call metadata tenant-wide (no per-user scoping) | `apps/voip/consumers.py:30-90`, `services.py:358-384` | Per-extension/admin groups; filter payload by role |
| `websockets-3` | TicketPresence join skips ticket-visibility check | `apps/tickets/consumers.py:150-156` | Enforce `agent_can_see_ticket` |
| `custom-fields-agents-3` | `visible_to_roles` unenforced; definitions visible/editable by any role | `apps/custom_fields/views.py:52-60` | Filter queryset by `effective_role`; gate writes |

### Confirmed info-leak on the live channel (High)
- **`websockets-2` (merged into `websockets-1` theme) / comments LiveBus:** `Comment.post_save` broadcasts `is_internal` comment **bodies** tenant-wide on `live_tenant_*`; clients are expected to filter. Any tenant member subscribing to the LiveBus receives internal note contents. **Fix:** omit body for internal comments server-side, or fan out only to authorized recipients.

## Residual hygiene (verified-not-exploitable, still fix)
- **`.env` on disk holds real Gmail/IMAP creds** (`settings-secrets-2`) — **not committed** (gitignored, verified), but rotate and ensure it never enters history; `INBOUND_EMAIL_WEBHOOK_SECRET=test-secret-123` is a weak dev value.
- **`SECRET_KEY`/`JWT_SECRET_KEY` fallback** — `JWT_SECRET_KEY` falls back to `SECRET_KEY`; ensure both are strong & distinct in prod.
- **`billing` `SubscriptionMiddleware` fail-open** on no-subscription (by design) — confirm intended for trials.

## Tenant-isolation threat model summary
The isolation model is **fail-closed by default** (good) but has **explicit exceptions** that are the weak points:
1. `InboundEmail` (plain manager, nullable tenant) — must be hand-filtered; already violated at `imap_poller.py:337`.
2. FKs to non-tenant-scoped `User` (assignee, mentions, DM/group participants) — must be membership-checked; currently not.
3. `Model.unscoped` usage in services/tasks — audit each for explicit `tenant=` filters.
4. `/media/` and the LiveBus channel carry data that bypasses per-user authz.

**Recommendation:** add a CI check/lint that flags `InboundEmail.objects` without `tenant=`, and `User.objects.filter` in tenant-scoped code paths.
