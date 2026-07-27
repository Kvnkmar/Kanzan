# API Surface — Verified 2026-05-22

> Verified against `main @ ea87bb2` (code state at `241e407`). Source of truth for **REST endpoints, custom actions, WebSocket consumers, permissions**. Pairs with `/CLAUDE.md`.

## REST routing

`main/urls.py` registers **22** `/api/v1/` `path()` includes (`/api/v1/inbound-email/` and `/api/v1/emails/` both mount `apps.inbound_email.api_urls` with different namespaces — so 21 unique URLConfs):

```
/api/v1/tenants/             apps.tenants.urls
/api/v1/accounts/            apps.accounts.urls
/api/v1/api-keys/            apps.api_keys.urls            ← NEW (fe0ad66)
/api/v1/tickets/             apps.tickets.urls
/api/v1/contacts/            apps.contacts.urls
/api/v1/billing/             apps.billing.urls
/api/v1/kanban/              apps.kanban.urls
/api/v1/comments/            apps.comments.urls
/api/v1/messaging/           apps.messaging.urls
/api/v1/notifications/       apps.notifications.urls
/api/v1/attachments/         apps.attachments.urls
/api/v1/analytics/           apps.analytics.urls
/api/v1/agents/              apps.agents.urls
/api/v1/custom-fields/       apps.custom_fields.urls
/api/v1/knowledge/           apps.knowledge.urls
/api/v1/notes/               apps.notes.urls
/api/v1/inbound-email/       apps.inbound_email.api_urls
/api/v1/emails/              apps.inbound_email.api_urls (namespace=emails_api)  ← alias mount
/api/v1/crm/                 apps.crm.urls
/api/v1/nav/                 apps.nav.urls
/api/v1/newsfeed/            apps.newsfeed.urls
/api/v1/voip/                apps.voip.urls
```

Plus: `/admin/`, `/accounts/` (allauth), `/api/schema/`, `/api/docs/`, frontend includes.

## Custom @action map per ViewSet

Counts include only `@action`-decorated methods; standard CRUD is implicit.

### tickets (`apps/tickets/views.py`) — `TicketViewSet` has ~36 `@action` decorators

**Mutations:** assign · close · change_status · change_stage · escalate · merge · split · restore
**Comments / activity / timeline:** comments (GET+POST) · activity · timeline · mark_all_read
**Email:** emails · send_email · send_creation_email · link_email · unlinked_emails
**Linking:** links (GET+POST) · delete_link
**Macros / bulk / search:** apply_macro (mapped to `update`) · bulk_action · search · lookup
**Watchers:** watchers (GET+POST) · watch · remove_watcher
**Time tracking:** time_entries (GET+POST) · time_summary · time_entry_detail
**Discovery:** teammates · team_progress

Permission stack: `[IsAuthenticated, HasTenantPermission, IsTicketAccessible]`, `permission_resource = "ticket"`. Manager+ gating is enforced **inside** merge/split/bulk-delete/team_progress rather than at the class level. `views.py` translates Django `ValidationError` → DRF `ValidationError` for clean 400s on illegal status transitions.

Other ticket-area ViewSets: `TicketStatusViewSet`, `TicketCategoryViewSet`, `QueueViewSet`, `SLAPolicyViewSet`, `EscalationRuleViewSet`, `BusinessHoursViewSet` (singleton), `PublicHolidayViewSet`, `CannedResponseViewSet` (`use`), `MacroViewSet`, `SavedViewViewSet` (`set_default`), `TicketTemplateViewSet` (`use`), `WebhookViewSet` (`test`, `reset_failures`), `CSATSubmitView` (public — `authentication_classes=[]`, `permission_classes=[]`; signed token validates caller).

### accounts
- `UserViewSet` (filters `is_service_account=False` out of staff lists)
- `ProfileViewSet`
- `RoleViewSet`
- `InvitationViewSet`
- `TenantMembershipViewSet`
- `UserGroupViewSet` — group CRUD + member add/remove
- `AuthViewSet` — register · login · logout · accept_invitation · change_password (**`throttle_scope = "auth"`** — only viewset in repo that opts into a scope)

### api_keys (NEW)
- `APIKeyViewSet` — **admin-only** (manage_api_keys permission, falls back to admin hierarchy). Standard CRUD + custom actions:
  - `regenerate` (POST detail) — mints a new cleartext key for the same APIKey row; returns once-only
  - `revoke` (POST detail) — sets `is_active=False`
- Creates write `ActivityLog` rows (`api_key_created`/`regenerated`/`revoked`) and queue `send_api_key_created_email_task` via `transaction.on_commit`. Cleartext returned **only** in the create/regenerate response.

### contacts
- `ContactViewSet` — bulk_action · context · timeline
- `CompanyViewSet` (annotates `contact_count`)
- `ContactGroupViewSet` — add_contacts · remove_contacts
- `AccountViewSet`

### tenants
- `TenantViewSet` (slug lookup; filters by user's memberships unless superuser), `TenantSettingsViewSet` (singleton; admin write, per-field Manager allowlist on `auto_transition_on_assign`/`auto_send_ticket_created_email`/`auto_assign_inbound_email_tickets`)

### billing
- `PlanViewSet` (`AllowAny`), `SubscriptionViewSet` (cancel · reactivate), `InvoiceViewSet`, `UsageViewSet`
- Non-ViewSet: `checkout`, `stripe_webhook` (CSRF-exempt; Stripe signature validated)

### kanban
- `BoardViewSet` (`detail_with_cards`, `populate`)
- `ColumnViewSet`
- `CardPositionViewSet` (`move` · `reorder` · `add_ticket`). `move`/`reorder` pass `actor=request.user, request=request` so Ticket status changes route through `apps.tickets.services.change_ticket_status` (full audit + feed + SLA path).

### comments
- `CommentViewSet` (replies · mark_read)
- `ActivityLogViewSet` (read-only)

### messaging
- `ConversationViewSet` (add_participant · leave · search_participants · remove_participant — `search_participants` bypasses `user.view` perm)
- `MessageViewSet` — **`broadcast`** (POST detail, author-only) re-emits the message over `chat_{conv_id}` after the client links attachments. `_broadcast_message` is a `@classmethod` that includes `attachments` + `author_name` (fallback `email` when blank) in the Channels payload.

### notifications
- `NotificationViewSet` (mark_read · unread_count · admin-only `cleanup`)
- `NotificationPreferenceViewSet`

### attachments
- `AttachmentViewSet` (`IsTenantMember`; `AttachmentUploadSerializer.validate()` enforces target object belongs to current tenant)

### analytics
- `DashboardView` (APIView)
- `ReportDefinitionViewSet`, `DashboardWidgetViewSet`, `ExportJobViewSet`, `CalendarEventViewSet`

### agents — `AgentAvailabilityViewSet` exposes 10 custom actions + `CustomAgentStatusViewSet`
- `set_status` (POST detail)
- `my_status` (GET/POST/PATCH list)
- `all_members` (GET list)
- `assignable_roles` (GET list, admin-only) — **excludes the `admin` slug** to prevent privilege escalation through the role picker; each entry includes a `description`
- `role_permissions/<role_id>` (GET, admin-only)
- `grant_temp_role` (POST detail, admin-only) — sets `TenantMembership.temporary_role` + curated `temporary_permissions` overrides
- `revoke_temp_role` (POST detail)
- `reactivate` (POST detail, admin-only)
- `online` (GET list)
- `workload` (GET list)
- `CustomAgentStatusViewSet` — tenant-scoped CRUD over `CustomAgentStatus` (slug + label + color)

### custom_fields
- `CustomFieldDefinitionViewSet` (reorder)
- `CustomFieldValueViewSet` (read-only)

### knowledge
- `CategoryViewSet`
- `ArticleViewSet` — submit_for_review · approve · reject · record_view · remove_file · preview_file (mammoth + sanitiser) · vote
- `KBSearchView` (search), feedback, vote endpoints

### notes
- `QuickNoteViewSet`

### inbound_email
- `InboundEmailViewSet` (read-only)
- `InboxViewSet` — `link` · `take_action` (`url_path="action"`) · `ignore` (agent inbox workflow)

### crm
- `ActivityViewSet` (my_tasks)
- `ReminderViewSet` — overdue · stats · complete · cancel · reschedule · bulk_action
- `PipelineForecastView`

### nav
- `BadgeCountView` — single GET endpoint `/badge-counts/` (capped at 99 per category)

### newsfeed — `NewsPostViewSet`
- react · mark_read · mark_all_read · unread_count (dynamic admin permission for create/update/destroy via `get_permissions()`)

### voip
- `VoIPSettingsViewSet`, `ExtensionViewSet`, `CallQueueViewSet`
- `CallLogViewSet` — active_calls · call_stats
- APIView endpoints: `InitiateCallView`, `CallHoldView`, `CallTransferView`, `CallHangupView`, `SIPCredentialsView`, `CallRecordingDownloadView`

## WebSocket consumers (6 endpoints across 5 routing files)

Wired in `main/asgi.py`:
```
ProtocolTypeRouter
├── HTTP   → Django ASGI
└── WS     → AllowedHostsOriginValidator
              → AuthMiddlewareStack
                → WebSocketTenantMiddleware
                  → URLRouter(messaging_ws + notification_ws + ticket_ws + voip_ws + live_ws)
```

`WebSocketTenantMiddleware` decodes the `Host` header from scope, resolves Tenant via subdomain or `domain` field, sets `scope["tenant"]` and binds `set_current_tenant()` for the lifetime of the connection.

| Endpoint                                         | Consumer                  | Group                     | Purpose                                                       |
|--------------------------------------------------|---------------------------|---------------------------|---------------------------------------------------------------|
| `ws/messaging/{conversation_id}/`                | `ChatConsumer`            | `chat_{conversation_id}`  | send_message, typing, mark_read; rate-limited (5/sec, 10KB, 2s typing cooldown); payload now carries `attachments[]` |
| `ws/notifications/`                              | `NotificationConsumer`    | `notifications_{user_id}` | per-user push; mark_read action                               |
| `ws/tickets/{ticket_id}/presence/`               | `TicketPresenceConsumer`  | `ticket_{id}_presence`    | agent_joined, agent_left; heartbeat. **`presence_list` is documented but unimplemented** — newcomers only see their own join until others trigger another broadcast |
| `ws/tickets/feed/`                               | `TicketListConsumer`      | `ticket_feed_{tenant_id}` | ticket_created/updated/assigned/closed/deleted broadcast; client-side `ticket-feed.js` republishes into `LiveBus` as `ticket.*` |
| `ws/voip/events/`                                | `CallEventConsumer`       | `voip_{tenant_id}`        | call_ringing/answered/ended/hold from ARI Stasis pipeline     |
| **`ws/live/`**                                   | **`LiveEventConsumer`**   | `live_tenant_{tenant_id}` | Read-only fan-out for newsfeed, CRM, contacts, comments, profile, membership events |

All tenant-scoped consumers verify tenant membership before joining the channel-layer group (anon → close `4001`; non-member → close `4003`). `ChatConsumer` additionally validates participant membership and closes `4002` (invalid UUID) / `4004` (cross-tenant conversation).

> **Note:** Tickets do NOT broadcast server-side to `live_tenant_*`. The bridge is **client-side** in `static/js/ticket-feed.js` (normalises `ticket_created` → `ticket.created`, plus an aggregated `ticket.event`). Authenticated tenant pages therefore hold two concurrent WebSockets (`ws/live/` + `ws/tickets/feed/`) plus notifications.

> **Known security caveat:** `Comment.is_internal` events are broadcast on the tenant-wide live channel. Clients are expected to filter. A non-agent user with an open live WS will receive the payloads — flagged for follow-up (per-role groups).

## Permission classes (`apps/accounts/permissions.py`)

- **`HasTenantPermission`** — codename-based (`{resource}.{action}`); `ACTION_MAP` maps 70+ DRF action names to `{resource}.{action}` (incl. `apply_macro` → `update`, `grant_temp_role`/`revoke_temp_role`, webhook `test`/`reset_failures`, `regenerate`/`revoke` for api-keys); falls back to hierarchy defaults when the membership has no permissions in its qs (view → ≤40, create/update → ≤30, delete/other → ≤20). Uses `membership.effective_role` and `has_effective_permission()` so `temporary_permissions` intersection is honoured.
- **`IsTicketAccessible`** — object-level row filtering. ≤20 bypass; otherwise `created_by_id == user.pk OR assignee_id == user.pk` (applies to Team Lead/Agent/IT/HR/Viewer).
- **`IsTenantMember`** — applied to `AttachmentViewSet`, `BoardViewSet`, `ColumnViewSet`, `CardPositionViewSet`, `ContactGroupViewSet`, `ConversationViewSet`, `MessageViewSet`, `NotificationViewSet`, `NotificationPreferenceViewSet`, `QuickNoteViewSet`, `ReminderViewSet`, `InboxViewSet`. Blocks cross-tenant JWT attacks.
- **`IsTenantAdmin`** — `hierarchy_level ≤ 10`.
- **`IsTenantAdminOrManager`** — `hierarchy_level ≤ 20`.
- Helper `_get_membership()` caches the membership on `request._cached_tenant_membership` for repeated checks within a request.

### Role hierarchy (post-`accounts/0011`, 7 system roles)

| Slug          | Level | Notes                                                                                           |
|---------------|-------|-------------------------------------------------------------------------------------------------|
| admin         | 10    | Full power. Excluded from `assignable_roles` to prevent privilege escalation.                   |
| manager       | 20    | Passes `is_admin_or_manager`; admits `_role_required(20)` pages.                                |
| **team-lead** | 25    | Elevated Agent — `ticket.delete/export`, `contact.delete/export`, `user.view`, ops viewers, `kb_article.delete`, `inbound_email.manage`. Satisfies `is_agent_or_above` but NOT `is_admin_or_manager`. |
| agent         | 30    | Row-scoped on tickets/contacts/reminders.                                                       |
| **it**        | 30    | Agent + `user.view`.                                                                            |
| **hr**        | 30    | Agent + `user.view`.                                                                            |
| viewer        | 40    | Permission-less by design — relies on ≤40 view fallback in `HasTenantPermission`.               |

Role-gated frontend routes use `_role_required(20)` (Admin + Manager): `/users/`, `/billing/`, `/agents/`, `/audit-log/`, `/groups/`. `_role_required(30)` on `/emails/` (admits Team Lead, Agent, IT, HR). **`/settings/` is `@_membership_required + @ensure_csrf_cookie`** — any member can load; API enforces admin-only writes with a per-field allowlist for Managers.

## REST framework configuration (`main/settings/base.py:210-237`)

- **DEFAULT_AUTHENTICATION_CLASSES** order: `JWTAuthentication` → **`apps.api_keys.authentication.APIKeyAuthentication`** → `SessionAuthentication`. JWT is tried first; the API-key class engages only when no `Bearer` header is present (returns `None`).
- **Default permission:** `IsAuthenticated`
- **Pagination:** `PageNumberPagination`, `PAGE_SIZE=50`
- **Filters:** `DjangoFilterBackend`, `SearchFilter`, `OrderingFilter`
- **Default throttles (applied to every viewset):** `ScopedRateThrottle`, **`apps.api_keys.throttling.APIKeyRateThrottle`**
- **Throttle rates:**
  - `auth=10/min` (ScopedRateThrottle — only `AuthViewSet` opts in via `throttle_scope`)
  - `api_default=200/min`, `api_heavy=30/min`, `webhook=60/min` (ScopedRateThrottle — defined but no current opt-in)
  - **`api_key=1000/hour`** — `APIKeyRateThrottle` is `SimpleRateThrottle`-based; auto-engages on any `request.auth = APIKey` request, returns `None` from `get_cache_key` for JWT/Session traffic
- **Renderers:** JSON + BrowsableAPI
- **Schema:** drf-spectacular (`SPECTACULAR_SETTINGS.TITLE="CRM Suite API"`). `apps.api_keys.extensions.APIKeyAuthScheme` is registered by `apps/api_keys/apps.py::ready()` so Swagger UI's "Authorize" dialog exposes an `ApiKeyAuth` option alongside the JWT bearer.

### Rate-limit headers
`apps.api_keys.middleware.RateLimitHeadersMiddleware` (slot 12 in the middleware stack) emits `X-RateLimit-Limit/Remaining/Reset` on responses when `APIKeyRateThrottle` stashed `(limit, remaining, reset_epoch)` on `request._crm_throttle_info`. Zero overhead for non-API-key traffic.

### Public / unauthenticated endpoints
- `POST /api/v1/tickets/csat/` — `CSATSubmitView` (signed token validates caller)
- `GET /api/v1/billing/plans/` — `PlanViewSet` with `[AllowAny]`
- `POST /api/v1/billing/webhook/` — `stripe_webhook` (HMAC validated)
- `AuthViewSet.register`/`login`/`accept_invitation` — `[AllowAny]`, `throttle_scope="auth"`

## Service modules (where business logic actually lives)

| Path                                          | What it provides                                                                            |
|-----------------------------------------------|---------------------------------------------------------------------------------------------|
| `apps/tickets/services.py`                    | create_ticket_activity, assign_ticket, transition_ticket_status, change_ticket_status, change_ticket_priority, close_ticket, escalate_ticket, log_ticket_comment, initialize_sla, validate_status_transition, resume_from_wait, bulk_update_tickets, log_sla_change, broadcast_ticket_event, apply_macro/render_macro, merge_tickets, split_ticket, record_first_response, transition_pipeline_stage. **Single source of truth for dual-log writes.** Service mutations set `instance._skip_signal_logging = True` so the post_save signal doesn't double-write. `ALLOWED_TRANSITIONS["waiting"]` includes `resolved` + `closed`. |
| `apps/tickets/sla.py`                         | get_effective_elapsed_minutes, sla_deadline_utc, business-hours math, pause-aware deadlines |
| `apps/tickets/email_service.py`               | send_ticket_email (entry point), reply/created/CSAT wrappers, RFC-compliant Message-IDs    |
| `apps/tickets/webhook_service.py`             | deliver_webhook (HMAC SHA-256), fire_webhooks (dispatch via Celery)                         |
| `apps/kanban/services.py`                     | `move_card(card_position, target_column, position, *, actor=None, request=None)` — when card is a Ticket AND target column has different status, calls `apps.tickets.services.change_ticket_status` for full audit/feed/SLA path. Non-Ticket content falls back to direct save. |
| `apps/api_keys/services.py`                   | mint / regenerate / revoke — each writes an `ActivityLog` row + queues email via `transaction.on_commit` |
| `apps/inbound_email/inbox_services.py`        | link_email_to_ticket, action_email (OPEN/ASSIGN/CLOSE), ignore_email                        |
| `apps/inbound_email/services.py`              | _maybe_auto_assign — gated by `TenantSettings.auto_assign_inbound_email_tickets`            |
| `apps/agents/services.py`                     | pick_email_agent (load + fairness picker — Agent/IT/HR all eligible at level 30), auto_assign_email_ticket |
| `apps/tenants/live.py`                        | broadcast_live_event(tenant, event, payload, *, immediate=False) — pub/sub entry point for the live broadcast layer; defers via `transaction.on_commit` |
| `apps/voip/services.py`                       | originate/hangup/hold/transfer wrappers, process_ari_event, _broadcast_call_event, billing limit checks |
| `apps/voip/ari_client.py`                     | `ARIClient` (async httpx) + `ARIEventListener` (WebSocket Stasis listener)                  |
