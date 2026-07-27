# Security Findings — on-disk `df9b29d` (pre-hardening checkout)

Threat model: an authenticated Agent (hierarchy level 30) and a **foreign-tenant user holding a
valid JWT**. Every finding verified from source with file:line; the Criticals were reproduced live
or spot-checked by hand. **This checkout LACKS origin's `73dfef1`/`b37b3be`/`7c7b3de` hardening**,
so these holes are open here even where origin has since closed them.

## The master enabler — JWT `tenant_id` claim is written but never enforced

`request.tenant` is resolved **purely from the Host header** (`apps/tenants/middleware.py:114-186`).
The JWT `tenant_id` claim is stamped at issuance *after* a membership check
(`apps/accounts/serializers.py:378`, plus a raw-slug `role` claim at `:379`) but is **never read or
enforced** — DRF uses stock `rest_framework_simplejwt.authentication.JWTAuthentication`
(`main/settings/base.py:219`); a repo-wide grep finds no code that reads the `tenant_id` claim off a
validated token. **So any valid JWT is a valid credential on any tenant's subdomain.** The only thing
between a foreign JWT and another tenant's data is a per-view membership check. Where that check is
missing (bare `[IsAuthenticated]` + queryset scoped only by the Host-controlled contextvar), isolation
collapses. (API-key auth *does* cross-check tenant — `apps/api_keys/authentication.py:74` — JWT/session
do not.)

**`HasTenantPermission` and `IsTenantMember` are SOUND** — they reject `membership is None`
(`permissions.py:147-149`, `:245-246`). Do NOT "fix" them. The holes are viewsets that omit them.

## Severity roll-up

| # | Finding | file:line | Fixed on origin? |
|---|---------|-----------|------------------|
| **C1** | Cross-tenant READ + WRITE of comments & audit log (bare auth + skip-when-None row filter) | `comments/views.py:187,343` | Yes (7c7b3de) — **open here** |
| **C2** | Unauthenticated cross-tenant KB file download | `attachments/media_views.py:26,94` + `knowledge/models.py:83` | **open** |
| **C3** | Prod boots `DEBUG=True` + live Gmail SMTP creds in `.env` | `.env` + `settings/__init__.py:12` | config — **open** |
| **C4** | Zero-perm RBAC seeding → every self-service tenant runs on the hierarchy floor | `tenants/signals.py:95` + `frontend_views.py:~490` | Yes (b37b3be) — **open here** |
| **C5** | Manager → Admin vertical privilege escalation | `accounts/views.py:158-167` + `serializers.py:151,172` | Yes (7c7b3de) — **open here** |
| **H1** | Messaging TICKET-conversation cross-tenant participant injection → cross-tenant notification/message push | `messaging/serializers.py:413-417,598-607` | Yes (7c7b3de) — **open here** |
| **H2** | `CallEventConsumer` accepts foreign JWTs, joins tenant-wide call group | `voip/consumers.py:30-51` | partly (uncommitted) — **open** |
| **H3** | Reminder `bulk_action` IDOR (row-restriction bypass + reassign to any global user) | `crm/views.py:665,694` | Yes (73dfef1) — **open here** |
| **H4** | Accounts `UserViewSet` + `UserGroupViewSet` cross-tenant member/group enumeration | `accounts/views.py:69-81,459-468` | Yes (7c7b3de) — **open here** |
| **M1** | `DashboardView` `IsAuthenticated`-only — cross-tenant analytics for non-members | `analytics/views.py:196` | narrowed on origin — **open here** |
| **M2** | Bare-auth shared resources (CannedResponse/Macro/SavedView) read+write cross-tenant | `tickets/views.py:2423,2524,2593` | **open** |
| **M3** | `KBSearchView` cross-tenant search (Postgres-only; raises on SQLite) | `knowledge/views.py:553` | **open** |
| **M4** | `ProfileViewSet` foreign-tenant profile row creation | `accounts/views.py:202,219` | **open** |

---

## CRITICAL

### C1 — Cross-tenant READ + WRITE of comments & audit log
`CommentViewSet` (`comments/views.py:187`) and `ActivityLogViewSet` (`:343`) use
`permission_classes = [permissions.IsAuthenticated]` — no membership check. The row restriction AND
the internal-note hiding both live *inside* `if membership and membership.effective_role.hierarchy_level > 20:`
(`:214`/`:361`; `qs.exclude(is_internal=True)` at `:229`). When `membership is None` (a foreign JWT, or an
offboarded user) the entire block is skipped → queryset stays `Comment.objects`/`ActivityLog.objects`
(auto-scoped to the **Host** tenant) = every row incl. `is_internal=True` staff notes and the full audit
trail (IPs, diffs, actor identities).
**Exploit:** foreign JWT → `GET https://victim.localhost/api/v1/comments/comments/` → 200, every internal
note. `CommentViewSet` also allows `create` (author need not be a member) → `POST` an internal note onto any
victim ticket → 201 cross-tenant write.

### C2 — Unauthenticated cross-tenant KB file download
`Article.file.upload_to = "tenants/knowledge/articles/%Y/%m/"` (`knowledge/models.py:83`);
`media_views.py:26` `PUBLIC_PREFIXES = ("tenants/logos/", "tenants/knowledge/")` served with no auth/no
tenant check (`:94`). `/media/` is also EXEMPT in TenantMiddleware. **Exploit:**
`curl https://any.localhost/media/tenants/knowledge/articles/2026/07/internal-playbook.pdf` → 200, no
session — including files on draft / internal / `allowed_groups`-restricted articles, any tenant.

### C3 — Production would boot with DEBUG=True + live Gmail SMTP
On-disk `.env`: `DJANGO_DEBUG=True`, `EMAIL_BACKEND=…smtp.EmailBackend`, `EMAIL_HOST=smtp.gmail.com`,
`EMAIL_HOST_USER=kvnkmar012@gmail.com`, `EMAIL_HOST_PASSWORD=<real 16-char Gmail app password>`,
`CRM_FLOWER_AUTH=admin:changeme`. `settings/__init__.py:12` loads `dev.py` when `DJANGO_DEBUG` is truthy
→ `DEBUG=True`, `ALLOWED_HOSTS=["*"]`, tracebacks leaking settings on every 500, and outbound mail sent live
via a real Gmail account whose credentials sit in the file. `main/checks.py::crm.E001` only fires on an
explicit `manage.py check --deploy`, which the runtime never forces.

### C4 — Zero-perm RBAC seeding → the RBAC floor
`_assign_default_role_permissions` (`tenants/signals.py:93-99`, runs in `Tenant.post_save`) resolves each
role via **`Role.objects.get(tenant=…, slug=…)`** (`:95`) — the tenant-scoped `TenantAwareManager`. Tenant
creation (self-service signup `frontend_views.py:~490`, `provision_tenant`, admin, shell, any test factory)
runs with **no tenant bound in context** → `Role.objects` fail-closes to `.none()` → `.get()` raises
`Role.DoesNotExist` → swallowed by the dedicated `except Role.DoesNotExist: continue` (`:96-97`; NOT the
blanket `except (ImportError, Exception)` at `:86`, which wraps only the imports). The 7 roles are created
(`:60` passes `tenant=` explicitly) but **every role gets 0 permissions**. Reproduced live twice.
**Blast radius — the "floor":** on a zero-perm tenant `HasTenantPermission` finds `effective_perms.exists()
== False` and falls through to the hierarchy default (`permissions.py:182-188`): `view→≤40`,
`create/update→≤30`, `delete/manage/assign/export→≤20`. The fine-grained codename model is inert. An **Agent
(30)** can then `create`/`update` any `HasTenantPermission` resource regardless of codename: create `Queue`s,
rewrite an `SLAPolicy`, create **Webhooks with an attacker-controlled URL** (`WebhookViewSet`
`permission_resource="settings"` → `settings.create` → ≤30) to exfiltrate every ticket event, update
`VoIPSettings` (incl. ari credentials if serializer-writable). Fix = `Role.objects.get` → `Role.unscoped.get`
(or wrap in `tenant_context(instance)`). The correct impl `defaults.provision_default_roles` exists but is DEAD.

### C5 — Manager → Admin vertical privilege escalation
`TenantMembershipViewSet` (`accounts/views.py:158-167`, `UpdateModelMixin`) is gated
`[IsAuthenticated, IsTenantAdminOrManager]` — a **Manager (level 20) passes**. `TenantMembershipSerializer.role`
is a writable `PrimaryKeyRelatedField` (`serializers.py:151`) whose queryset is `Role.objects.filter(tenant=tenant)`
(`:158`, includes the `admin` role); `read_only_fields = ["id","user","tenant","invited_by","joined_at"]`
(`:172`) — **`role` is NOT read-only**, and there is no validation that the assigned role is ≤ the actor's own
level. **Exploit:** Manager `PATCH /api/v1/accounts/memberships/<id>/ {"role":"<admin_role_uuid>"}` → membership
becomes Admin → full tenant admin. Works on any tenant, seeded or not.
> Note: `grant_temp_role` is NOT a vector — it is admin-gated and explicitly refuses `role.slug == "admin"`.

---

## HIGH

### H1 — Messaging TICKET-conversation cross-tenant participant injection
DIRECT (`messaging/serializers.py:373-378`) and manual GROUP (`:397-411`) branches membership-check every
`user_id`. The **TICKET branch validates only `ticket_id`** (`:414-417`); `_create_ticket` then
`bulk_create`s `ConversationParticipant(user_id=uid)` straight from `data.get("user_ids", [])` (`:598-607`)
with no membership check. **Exploit:** tenant-A member creates a TICKET conversation with a tenant-B user's
UUID → on any posted message `notify_new_message` fans to all participants → `send_notification(recipient=B,
tenant=A, body=preview)` → group_send to `notifications_{B}`. `send_notification` (`notifications/services.py:33,112`)
has **no recipient-membership guard**; `NotificationConsumer` (`consumers.py:39-64`) joins `notifications_{user.id}`
with no membership check → tenant-A message preview lands on tenant-B's screen. A tenant-A user can push
arbitrary text to any user in the system by UUID.

### H2 — CallEventConsumer accepts foreign JWTs, tenant-wide group
`voip/consumers.py:30-44`: connect checks only `user` authed + Host `tenant` present, then joins
`voip_{tenant.id}` with **no `TenantMembership` check and no per-user scoping**. **Exploit:** foreign user
connects `wss://victim.localhost/ws/voip/events/` → receives every `call_ringing/answered/ended/hold` event
(caller/callee numbers, metadata) for the whole victim tenant. (VoIP is otherwise structurally non-functional
by default — no ARI listener + unconsumed `crm_voip` queue — but this WS surface is live.)

### H3 — Reminder bulk_action IDOR
`ReminderViewSet` is `[IsAuthenticated, IsTenantMember]` (cross-tenant blocked), but `bulk_action`
(`crm/views.py:665`) targets `Reminder.objects.filter(id__in=…)` — **NOT `self.get_queryset()`**, whose agent
row filter `Q(assigned_to=user)|Q(created_by=user)` (for `effective_role.hierarchy_level > 20`) lives at
`:390-393`. The `:667` guard checks `found_count != len` (tenant visibility only, not ownership). Reassign
(`:694`) resolves `User.objects.filter(pk=…)` — **any global user, no tenant scope**. **Exploit:** an Agent
`POST /api/v1/crm/reminders/bulk-action/` with `action=complete|cancel|reschedule|reassign` and teammates'
reminder UUIDs → mutates them all; `reassign` can point `assigned_to` at any User row in the DB.
`bulk_action` also writes no audit row and no LiveBus (bare `.update()`).

### H4 — Accounts UserViewSet + UserGroupViewSet cross-tenant enumeration
Both downgrade `get_permissions()` to `[IsAuthenticated()]` for list/retrieve (dropping `HasTenantPermission`)
while `get_queryset()` scopes only by the Host `request.tenant` (`accounts/views.py:69-81`, `:459-468`).
Reproduced live: a member of `straat-x` only, hitting a different tenant's subdomain, `GET /api/v1/accounts/users/`
→ returns the foreign tenant's roster (id, email, first/last name, phone, avatar); same for `/user-groups/`.

---

## MEDIUM

- **M1** `DashboardView` `[IsAuthenticated]`-only (`analytics/views.py:196`); `_is_admin_or_manager` only gates
  `agent_performance`/`sla_compliance`. All other blocks (ticket_stats, summary, hourly_trends,
  unresolved_by_queue, overdue) return to any authenticated user for the Host tenant → cross-tenant aggregate leak.
- **M2** `CannedResponseViewSet`/`MacroViewSet`/`SavedViewViewSet` (`tickets/views.py:2423,2524,2593`) bare
  `[IsAuthenticated]`; `get_queryset = <Model>.objects.filter(Q(is_shared=True)|Q(created_by=user))` (Host-scoped).
  Foreign JWT reads the victim tenant's shared canned responses/macros/saved views and can `create` new ones in it.
- **M3** `KBSearchView` (`knowledge/views.py:553`) `[IsAuthenticated]` — foreign non-member searches the victim
  tenant's published KB (title/snippet leak). Bounded to published + agent-visibility. Raises on SQLite
  (`search.py` has no `icontains` fallback despite the docstring) → Postgres-only leak.
- **M4** `ProfileViewSet` (`accounts/views.py:202`) `me` action creates a Profile row in the victim tenant for a
  foreign user (`get_or_create(user=request.user, tenant=Host)`). Reads self-scoped → low-impact pollution.

## REFUTED (verified NOT present on this checkout — do not carry these)

- **Stored XSS via inbound-email subject/body — REFUTED for that vector.** Every render path escapes/sanitizes:
  cockpit `inbox-hub.js` (`esc()`=textContent; body via `DOMPurify.sanitize(BODY_SANITIZE_CONFIG)` with no
  script/on*/svg), personal Inbox `emails/list.html` (all textContent), ticket detail (`DOMPurify.sanitize`),
  notification flyout (`escapeHtmlGlobal`/textContent). No `|safe` on email content.
  **BUT** `knowledge/views.py::preview_file` (`:415-424`) "sanitizes" mammoth-rendered DOCX with two regexes
  (strip `<script>` + `on\w+=`) — trivially bypassable (`<img src=x onerror=…>`, `<svg>`, `javascript:` hrefs) →
  a credible stored-XSS vector. So the refutation is scoped, not blanket.
- **`grant_temp_role` Manager→Admin — REFUTED.** Admin-gated + refuses `role.slug == "admin"`. The real
  escalation is C5 (a different route).
- **`HasTenantPermission` fail-open — REFUTED.** It rejects `membership is None`; it is sound.

## Correctly-hardened already (do not regress)
comments internal-body LiveBus redaction (`comments/signals.py:39`); messaging DIRECT/GROUP + mention +
add-participant tenant scoping; attachments object-level authz + `tenants/<uuid>/…` media gate; Stripe webhook
replay guard (marker set only after handler success); billing re-subscribe repoint; `fire_due_reminders`
claim-first dedup; notes strictly per-user; API-key SHA-512 + cross-tenant guard + fail-open-only-on-absent-header;
`LiveEventConsumer` DOES membership-check before joining `live_tenant_{pk}`.
