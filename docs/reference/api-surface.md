# API Surface — Verified 2026-05-11

> Verified against `main @ bb36325`. Source of truth for **REST endpoints, custom actions, WebSocket consumers, permissions**.

## REST routing

`main/urls.py` registers **21** `/api/v1/` includes (the existing CLAUDE.md said 22 — the discrepancy is that `/api/v1/inbound-email/` and `/api/v1/emails/` both mount the same `apps.inbound_email.api_urls` URLConf with different namespaces):

```
/api/v1/tenants/             apps.tenants.urls
/api/v1/accounts/            apps.accounts.urls
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

### tickets (`apps/tickets/views.py`) — TicketViewSet has **31 custom actions**
**Mutations:** assign · close · change_status · change_stage · escalate · merge · split · restore
**Comments / activity / timeline:** comments (GET+POST) · activity · timeline · mark_all_read
**Email:** emails · send_email · send_creation_email · link_email · unlinked_emails
**Linking:** links (GET+POST) · delete_link
**Macros / bulk / search:** apply_macro · bulk_action · search · lookup
**Watchers:** watchers (GET+POST) · watch · remove_watcher
**Time tracking:** time_entries (GET+POST) · time_summary · time_entry_detail
**Discovery:** teammates · team_progress

Other ticket-area ViewSets: `TicketStatusViewSet`, `TicketCategoryViewSet`, `QueueViewSet`, `SLAPolicyViewSet`, `EscalationRuleViewSet`, `BusinessHoursViewSet`, `PublicHolidayViewSet`, `CannedResponseViewSet` (`use`), `MacroViewSet`, `SavedViewViewSet` (`set_default`), `TicketTemplateViewSet` (`use`), `WebhookViewSet` (`test`, `reset_failures`), `CSATSubmitView` (public, no auth).

### accounts
- `UserViewSet`
- `ProfileViewSet`
- `RoleViewSet`
- `InvitationViewSet`
- `TenantMembershipViewSet`
- `AuthViewSet` — actions: register · login · logout · accept_invitation · change_password (throttle scope `auth`)

### contacts
- `ContactViewSet` — actions: bulk_action · context · timeline
- `CompanyViewSet` (annotates `contact_count`)
- `ContactGroupViewSet` — actions: add_contacts · remove_contacts

### tenants
- `TenantViewSet`, `TenantSettingsViewSet`

### billing
- `PlanViewSet`, `SubscriptionViewSet` (cancel · reactivate), `InvoiceViewSet`, `UsageViewSet`
- Non-ViewSet: checkout, webhook (CSRF-exempt)

### kanban
- `BoardViewSet` (detail_with_cards · populate)
- `ColumnViewSet`
- `CardPositionViewSet` (move · reorder)

### comments
- `CommentViewSet` (replies · mark_read)
- `ActivityLogViewSet` (read-only)

### messaging
- `ConversationViewSet` (add_participant · leave · search_participants · remove_participant)
- `MessageViewSet`

### notifications
- `NotificationViewSet`, `NotificationPreferenceViewSet`

### attachments
- `AttachmentViewSet`

### analytics
- `DashboardView`
- `ReportDefinitionViewSet`, `DashboardWidgetViewSet`, `ExportJobViewSet`, `CalendarEventViewSet`

### agents — `AgentAvailabilityViewSet` exposes **9 custom actions**
- `set_status` (POST detail)
- `my_status` (GET/POST/PATCH list)
- `all_members` (GET list)
- `assignable_roles` (GET list)
- `grant_temp_role` (POST detail) — **temporary-role override grant**
- `revoke_temp_role` (POST detail) — **temporary-role override revoke**
- `reactivate` (POST detail)
- `online` (GET list)
- `workload` (GET list)

### custom_fields
- `CustomFieldDefinitionViewSet` (reorder)
- `CustomFieldValueViewSet`

### knowledge
- `CategoryViewSet`
- `ArticleViewSet` actions: submit_for_review · approve · reject · record_view · remove_file · preview_file · vote
- `KBSearchView` (search), feedback, vote endpoints

### notes
- `QuickNoteViewSet`

### inbound_email
- `InboundEmailViewSet` (read-only)
- `InboxViewSet` actions: link · take_action (`url_path="action"`) · ignore — agent inbox workflow

### crm
- `ActivityViewSet` (my_tasks)
- `ReminderViewSet` actions: overdue · stats · complete · cancel · reschedule · bulk_action
- `PipelineForecastView`

### newsfeed — `NewsPostViewSet`
- react · mark_read · mark_all_read · unread_count

### nav
- `BadgeCountView` — single GET endpoint `/badge-counts/` (capped at 99 per category)

### voip
- `VoIPSettingsViewSet`, `ExtensionViewSet`, `CallQueueViewSet`
- `CallLogViewSet` actions: active_calls · call_stats
- APIView endpoints: `InitiateCallView`, `CallHoldView`, `CallTransferView`, `CallHangupView`, `SIPCredentialsView`, `CallRecordingDownloadView`

## WebSocket consumers (5 endpoints across 4 routing files)

Wired in `main/asgi.py`:
```
ProtocolTypeRouter
├── HTTP   → Django ASGI
└── WS     → AllowedHostsOriginValidator
              → AuthMiddlewareStack
                → WebSocketTenantMiddleware
                  → URLRouter(messaging_ws + notification_ws + ticket_ws + voip_ws)
```

| Endpoint                                         | Consumer                  | Group                     | Purpose                                                       |
|--------------------------------------------------|---------------------------|---------------------------|---------------------------------------------------------------|
| `ws/messaging/{conversation_id}/`                | `ChatConsumer`            | `chat_{conversation_id}`  | send_message, typing, mark_read; rate-limited (5/sec, 10KB)   |
| `ws/notifications/`                              | `NotificationConsumer`    | `notifications_{user_id}` | per-user push; mark_read action                               |
| `ws/tickets/{ticket_id}/presence/`               | `TicketPresenceConsumer`  | `ticket_{id}_presence`    | agent_joined, agent_left, presence_list; heartbeat            |
| `ws/tickets/feed/`                               | `TicketListConsumer`      | `ticket_feed_{tenant_id}` | ticket_created/updated/assigned/closed/deleted broadcast      |
| `ws/voip/events/`                                | `CallEventConsumer`       | `voip_{tenant_id}`        | call_ringing/answered/ended/hold from ARI Stasis pipeline     |

All consumers verify tenant membership from scope; `ChatConsumer` additionally validates participant membership.

## Permission classes (`apps/accounts/permissions.py`)

- **HasTenantPermission** — codename-based (`{resource}.{action}`); `ACTION_MAP` has 50+ mappings (including `apply_macro` → `update`, `grant_temp_role` / `revoke_temp_role`, webhook `test` / `reset_failures`); fallback hierarchy defaults via `_FALLBACK` (view ≤40, create/update ≤30, delete/manage ≤20). Uses `membership.effective_role` (respects temporary-role overrides).
- **IsTicketAccessible** — object-level. Admin/Manager bypass; Agent/Viewer see only own/assigned.
- **IsTenantMember** — applied to `AttachmentViewSet`, `BoardViewSet`, `ColumnViewSet`, `CardPositionViewSet`, `ContactGroupViewSet`, `ConversationViewSet`, `MessageViewSet`, `NotificationViewSet`, `NotificationPreferenceViewSet`, `QuickNoteViewSet`, `InboxViewSet`. Blocks cross-tenant JWT attacks.
- **IsTenantAdmin** — `hierarchy_level <= 10`.
- **IsTenantAdminOrManager** — `hierarchy_level <= 20`.

## Frontend role gating

`apps/tenants/frontend_urls.py` uses `_role_required(20)` (admin+manager) on:
- `/users/`, `/settings/`, `/billing/`, `/agents/`, `/audit-log/`

(`/emails/` and `/calls/` are open to any authenticated tenant member.)

## REST framework configuration

- **Auth:** SimpleJWT (15m access, 7d refresh, rotate+blacklist, HS256) + SessionAuthentication
- **Permissions default:** IsAuthenticated
- **Pagination:** PageNumberPagination, PAGE_SIZE=50
- **Filters:** DjangoFilterBackend, SearchFilter, OrderingFilter
- **Throttle scopes:** `auth=10/min`, `api_default=200/min`, `api_heavy=30/min`, `webhook=60/min` (ScopedRateThrottle)
- **Schema:** drf-spectacular (`/api/schema/` JSON, `/api/docs/` Swagger UI)
- **Renderers:** JSON + BrowsableAPI

## Service modules (where business logic actually lives)

| Path                                          | What it provides                                                                            |
|-----------------------------------------------|---------------------------------------------------------------------------------------------|
| `apps/tickets/services.py`                    | create_ticket_activity, assign_ticket, transition_ticket_status, change_ticket_priority, close_ticket, escalate_ticket, log_ticket_comment, initialize_sla, validate_status_transition, resume_from_wait, bulk_update_tickets, log_sla_change, broadcast_ticket_event, etc. — single source of truth for dual-log writes |
| `apps/tickets/sla.py`                         | get_effective_elapsed_minutes, sla_deadline_utc, business-hours math, pause-aware deadlines |
| `apps/tickets/email_service.py`               | send_ticket_email (entry point), reply/created/CSAT wrappers, message-id generation         |
| `apps/tickets/webhook_service.py`             | deliver_webhook (HMAC SHA-256), fire_webhooks (dispatch via Celery)                         |
| `apps/inbound_email/inbox_services.py`        | link_email_to_ticket, action_email (OPEN/ASSIGN/CLOSE), ignore_email                        |
| `apps/inbound_email/services.py`              | _maybe_auto_assign — gated by `TenantSettings.auto_assign_inbound_email_tickets`            |
| `apps/agents/services.py`                     | pick_email_agent (load + fairness picker), auto_assign_email_ticket                         |
| `apps/voip/services.py`                       | originate/hangup/hold/transfer wrappers, process_ari_event, _broadcast_call_event, billing limit checks |
| `apps/voip/ari_client.py`                     | `ARIClient` (async httpx) + `ARIEventListener` (WebSocket Stasis listener)                  |
