# Codebase Inventory — Verified 2026-05-22

> Verified against `main @ ea87bb2` (post-CLAUDE.md 2026-05-22 refresh; code state at `241e407`). Source of truth for **what models, migrations, signals, tasks, and management commands exist**. Pairs with the project-intelligence narrative in `/CLAUDE.md`.

## Apps directory (21 Django apps under `apps/`)

`apps.nav` is URL-only — no `models.py`, no `AppConfig`, no migrations — so its row is intentionally empty.

| App              | Models | Latest migration                                                          | Tasks | Signals | Notes                                                                |
|------------------|--------|---------------------------------------------------------------------------|-------|---------|----------------------------------------------------------------------|
| accounts         | 8      | `0011_seed_team_lead_it_hr_roles`                                         | 0     | 1       | User (+is_service_account), Permission, Role, Profile, TenantMembership (+temporary_permissions M2M), Invitation, **UserGroup**, EmailVerificationToken |
| agents           | 2      | `0006_customagentstatus_…_custom_status`                                  | 0     | 0       | `AgentAvailability` + `CustomAgentStatus`                            |
| analytics        | 4      | `0003_calendarevent_color_calendarevent_end_date_and_more`                | 1     | 0       | ReportDefinition + DashboardWidget + ExportJob + CalendarEvent       |
| **api_keys**     | 1      | `0001_initial`                                                            | 1     | 0       | APIKey (TenantScoped service-account creds; SHA-512 hash; cleartext returned once at mint/regenerate) |
| attachments      | 1      | `0001_initial`                                                            | 0     | 0       | Attachment (polymorphic GenericFK)                                   |
| billing          | 4      | `0002_plan_has_call_recording_plan_has_voip_and_more`                     | 0     | 0       | Plan with VoIP feature flags + audit_retention_days                  |
| comments         | 4      | `0009_alter_activitylog_action`                                           | 0     | 1       | `ActivityLog` has **26** action choices (api_key_created/regenerated/revoked added in 0009) |
| contacts         | 5      | `0005_widen_phone_field`                                                  | 0     | 1       | Account + Company + Contact + ContactGroup + ContactEvent            |
| crm              | 2      | `0004_reminder_m2m_contacts_tickets`                                      | 3     | 1       | **`Reminder` M2M migration is the gotcha** — `contact`/`ticket` singular FKs are gone |
| custom_fields    | 2      | `0001_initial`                                                            | 0     | 1       | EAV; signals sync from `custom_data` JSON                            |
| inbound_email    | 3      | `0008_alter_inboundemail_recipient_email_imappollstate`                   | 2     | 0       | InboundEmail + BounceLog + IMAPPollState (uid_validity + last_uid watermark) |
| kanban           | 3      | `0004_board_is_personal`                                                  | 0     | 0       | Polymorphic `CardPosition`; `Board.is_personal` (personal boards private to creator) |
| knowledge        | 6      | `0005_article_allowed_groups`                                             | 2     | 1       | Article + KBRevision + KBVote + KBSearchGap + KBTicketLink; `Article.allowed_groups` M2M to UserGroup |
| messaging        | 3      | `0002_conversation_source_group`                                         | 0     | 0       | Conversation + Participant + Message; `Conversation.source_group` FK to UserGroup |
| nav              | 0      | (no migrations folder)                                                    | 0     | 0       | URL-only stub — `/api/v1/nav/badge-counts/` only                     |
| newsfeed         | 3      | `0002_reactions_reads_enhancements`                                       | 0     | 1       | NewsPost + Reaction + Read                                           |
| notes            | 1      | `0001_initial`                                                            | 0     | 0       | QuickNote — 6 colors                                                 |
| notifications    | 2      | `0004_rename_recall_to_reminder`                                          | 2     | 0       | Notification + NotificationPreference (not polymorphic — `data` JSONField only). Custom signal *handlers* in `signal_handlers.py`. |
| tenants          | 2      | `0008_tenantsettings_auto_assign_inbound_email_tickets`                   | 0     | 1       | Tenant + TenantSettings + auto-assign toggle                         |
| tickets          | 22     | `0026_alter_ticketactivity_event`                                         | 10    | 1       | `TicketActivity` has **27** event choices; `Ticket.save()` auto-fills `company` from linked contact |
| voip             | 5      | `0002_voipsettings_asterisk_use_ssl_and_more`                             | 3     | 1       | Asterisk ARI integration                                             |

**Totals:** 21 apps · **83 model classes** (excludes `TextChoices` / `Manager` / `QuerySet` definitions; nav has none) · ~24 Celery tasks · 10 `signals.py` modules + `notifications/signal_handlers.py`.

## Detailed model breakdown

### accounts (8)
`User` (custom AbstractUser, UUID PK, `auth_version` for global logout, **`is_service_account` boolean** db_indexed; minted by `apps.api_keys` for hidden synthetic users), `Permission` (**global** — not tenant-scoped), `Role` (TenantScoped, M2M permissions, `hierarchy_level`, `is_system`), `Profile` (TenantScoped), `TenantMembership` (joins user↔tenant; `temporary_role` + `temporary_role_expires_at` + **M2M `temporary_permissions`** for intersection scoping), `Invitation`, **`UserGroup`** (TenantScoped, M2M `members` → User; surfaces in `Article.allowed_groups` and `Conversation.source_group`), `EmailVerificationToken`.

### agents (2)
`AgentAvailability` (TenantScopedModel — status enum + `max_concurrent_tickets` + `current_ticket_count` + `working_hours` JSON + `auto_away_outside_hours` + `custom_status` FK), **`CustomAgentStatus`** (tenant-scoped — slug + label + color via `StatusColor` 8-choice; `color_hex` property maps slug→hex). `BUILTIN_STATUS_SLUGS = frozenset(AgentStatus.values)` module constant.

### analytics (4)
`ReportDefinition`, `DashboardWidget`, `ExportJob`, `CalendarEvent` (`color`/`end_date` added in 0003)

### api_keys (1) — NEW
`APIKey(TenantScopedModel)` — fields: `name`, `service_user` (OneToOne to hidden synthetic `User` with `is_service_account=True`, CASCADE), `role` (FK PROTECT — drives `HasTenantPermission`), `prefix` (first ~20 chars, indexed), `hashed_key` (SHA-512 hex; **cleartext never persisted**), `created_by` (PROTECT), `is_active`, `expires_at`, `last_used_at`/`last_used_ip`/`last_used_user_agent`, `request_count`. `unique_together=("tenant","name")`. Cleartext format: `kz_live_<slug6>_<token_urlsafe(32)>` — returned exactly once at create/regenerate, unrecoverable afterward.

### attachments (1)
`Attachment` (polymorphic GenericFK; tenant-scoped path `tenants/{tenant_id}/attachments/YYYY/MM/{filename}`)

### billing (4)
`Plan` (Free/Pro/Enterprise + VoIP flags + audit_retention_days), `Subscription` (1:1 Tenant, `in_grace_period` derived), `Invoice`, `UsageTracker`

### comments (4)
`Comment` (polymorphic, threaded, `is_internal`), `Mention`, `CommentRead`, `ActivityLog` (**26** action choices incl. `api_key_created/regenerated/revoked`; polymorphic audit trail)

### contacts (5)
`Account`, `Company`, `Contact`, `ContactGroup`, `ContactEvent` (append-only 360° timeline; intentionally **NOT** live-broadcast)

### crm (2)
`Activity`, `Reminder` (M2M `contacts` + `tickets` since migration 0004; `status` is a derived property; has `unscoped` manager). No `services.py` — deal fields live on `Ticket`.

### custom_fields (2)
`CustomFieldDefinition`, `CustomFieldValue` (EAV, polymorphic, 4 typed value columns with indexes)

### inbound_email (3)
`InboundEmail` (`TimestampedModel` — **NOT** `TenantScopedModel`; `tenant` nullable until parsing resolves a tenant), `BounceLog`, `IMAPPollState` (`uid_validity` + `last_uid` watermark — never-backfill safety)

### kanban (3)
`Board` (`resource_type` TICKET/DEAL, `is_default`, **`is_personal`** — personal boards private to creator), `Column`, `CardPosition` (polymorphic GenericFK). Kanban drags route Ticket status changes through `apps.tickets.services.change_ticket_status` (full audit + feed + SLA path).

### knowledge (6)
`Category`, `Article` (`allowed_groups` M2M to UserGroup since migration 0005; `Article.save()` resolves tenant from `main.context.get_current_tenant()` before slug-uniqueness scan; falls back to `"article"` when `slugify(title)` is empty), `KBRevision`, `KBVote` (session-keyed; unique per article+session), `KBSearchGap`, `KBTicketLink`

### messaging (3)
`Conversation` (DIRECT/GROUP/TICKET; **`source_group`** FK to UserGroup added in 0002 — dedups per-creator group conversations), `ConversationParticipant`, `Message`. `MessageCreateSerializer.body` is `allow_blank=True, required=False, default=""` — attachment-only messages valid serializer-side.

### newsfeed (3)
`NewsPost` (5 categories), `NewsPostReaction` (6 emoji), `NewsPostRead` (NOT tenant-scoped — row existence = read)

### notes (1)
`QuickNote` (6 colors, pinning)

### notifications (2)
`Notification` (15 types — **NOT polymorphic**; only a `data` JSONField), `NotificationPreference`. Handlers live in `signal_handlers.py` (not `signals.py`).

### tenants (2)
`Tenant`, `TenantSettings` (defaults `primary_color="#6366F1"`/`accent_color="#F59E0B"`; fallback in `derive_palette` is Crimson Black `#C1121F`/`#E11D2D` if hex parsing fails)

### tickets (22)
`Pipeline`, `PipelineStage`, `TicketStatus`, `Queue`, `TicketCategory`, `TicketCounter` (NOT TenantScoped; SELECT FOR UPDATE counter), `Ticket` (~64 fields; `save()` auto-populates `company` from linked contact when unset — never overwrites), `TicketLink` (circular-link BFS guard), `SLAPolicy`, `EscalationRule`, `BusinessHours`, `PublicHoliday`, `SLAPause`, `TicketActivity` (**27** event choices), `CannedResponse`, `Macro`, `SavedView`, `TicketAssignment`, `TicketWatcher`, `TimeEntry`, `TicketTemplate`, `Webhook` (HMAC SHA-256; 8 EventType; auto-disable at 10 failures)

### voip (5)
`VoIPSettings`, `Extension`, `CallLog`, `CallRecording`, `CallQueue`

### Polymorphic models (5 total)
`Attachment`, `Comment`, `ActivityLog`, `CustomFieldValue`, `CardPosition`. **Not** `Notification`.

## Signals (10 `signals.py` modules + `notifications/signal_handlers.py`)

| App           | File                              | Responsibilities                                                                                    |
|---------------|-----------------------------------|-----------------------------------------------------------------------------------------------------|
| tenants       | `apps/tenants/signals.py`         | `Tenant.post_save (created=True)` → `create_tenant_settings` + `create_default_roles` (seeds **all 7** system roles inline) + `_assign_default_role_permissions` (iterates `ROLE_DEFINITIONS`) |
| accounts      | `apps/accounts/signals.py`        | `TenantMembership.post_save` → create_profile_on_membership + `broadcast_membership_save`; `Membership.post_delete` → broadcast; `Profile.post_save` → broadcast; `User.post_save` → broadcast across every active membership |
| **comments**  | `apps/comments/signals.py`        | `Comment.post_save/delete` → live broadcast `comment.created/.updated/.deleted` (payload includes `content_type="app_label.model"` + `object_id`) |
| **contacts**  | `apps/contacts/signals.py`        | `Contact`, `Company`, `Account`, `ContactGroup` × `post_save/delete` → live broadcast (`ContactEvent` intentionally skipped — too noisy) |
| **crm**       | `apps/crm/signals.py`             | `Activity`, `Reminder` × `post_save/delete` → live broadcast. Reminder verb resolved by state (cancelled/completed/created/updated) |
| custom_fields | `apps/custom_fields/signals.py`   | `Ticket.post_save` / `Contact.post_save` → sync `CustomFieldValue` rows from `custom_data` JSON     |
| knowledge     | `apps/knowledge/signals.py`       | `Article.post_save` → update Postgres `search_vector` (skipped on non-Postgres backends)            |
| **newsfeed**  | `apps/newsfeed/signals.py`        | `NewsPost.post_save/delete`, `NewsPostReaction.post_save/delete` → live broadcast (`newsfeed.reacted` carries `added: bool`) |
| tickets       | `apps/tickets/signals.py`         | 10 receivers: pre_save status change, post_save activity logging (respects `_skip_signal_logging` flag), kanban sync, SLA pause/resume, custom `ticket_created`/`ticket_assigned`/`ticket_closed` signals (consumed by webhooks + notifications), `SLAPolicy.post_save` deadline recalc |
| voip          | `apps/voip/signals.py`            | `CallLog.post_save` on terminal status → TicketActivity + ActivityLog + queue recording task        |

> Bold rows are part of the **live broadcast layer** (committed at 241e407) — they call `apps.tenants.live.broadcast_live_event` which `transaction.on_commit`-defers a fan-out to the `live_tenant_{tenant_id}` channel-layer group consumed by `LiveEventConsumer`.

### Notification signal handlers (`apps/notifications/signal_handlers.py`)
- `@receiver(ticket_assigned)` → `handle_ticket_assigned` (creates Notification, queues email task)
- `@receiver(ticket_comment_created)` → `handle_comment_notification` (+ private `_queue_contact_reply_email`)

## Celery tasks (~24 across 8 apps)

| App           | Tasks (registered name in parens if different)                                                   |
|---------------|--------------------------------------------------------------------------------------------------|
| tickets       | `check_sla_breaches`, `check_overdue_tickets`, `check_sla_breach_warnings`, `propagate_sla_policy_change_task`, `send_ticket_reply_email_task`, `send_ticket_created_email_task`, `send_ticket_email_task`, `auto_close_ticket`, `send_csat_survey_email`, `deliver_webhook_task` |
| notifications | `send_notification_email`, `cleanup_old_notifications`                                           |
| inbound_email | `fetch_inbound_emails_task`, `process_inbound_email_task`                                        |
| knowledge     | `alert_stale_articles` (`knowledge_base.alert_stale_articles`), `send_gap_digest` (`knowledge_base.send_gap_digest`) |
| crm           | `check_overdue_reminders` (NOT in Beat), `calculate_lead_scores`, `calculate_account_health_scores` |
| voip          | `process_call_recording`, `cleanup_stale_calls`, `sync_call_state`                               |
| analytics     | `process_export_job`                                                                             |
| **api_keys**  | `send_api_key_created_email_task` (bound, retries=3, default_retry_delay=60s, acks_late, queue `kanzan_email`; cleartext NOT included in email) |

### Beat schedule (`main/settings/base.py CELERY_BEAT_SCHEDULE`, 9 entries)

| Entry                         | Task                                              | Schedule                            |
|-------------------------------|---------------------------------------------------|-------------------------------------|
| check-sla-breaches            | apps.tickets.tasks.check_sla_breaches             | 120s                                |
| cleanup-old-notifications     | apps.notifications.tasks.cleanup_old_notifications| 86400s (daily)                      |
| check-overdue-tickets         | apps.tickets.tasks.check_overdue_tickets          | 900s (15m)                          |
| calculate-lead-scores         | apps.crm.tasks.calculate_lead_scores              | 86400s (daily)                      |
| calculate-account-health-scores | apps.crm.tasks.calculate_account_health_scores  | 86400s (daily)                      |
| kb-stale-alert                | knowledge_base.alert_stale_articles               | crontab daily 08:00                 |
| kb-gap-digest                 | knowledge_base.send_gap_digest                    | crontab Monday 09:00                |
| cleanup-stale-calls           | apps.voip.tasks.cleanup_stale_calls               | 3600s (hourly)                      |
| fetch-inbound-emails          | apps.inbound_email.tasks.fetch_inbound_emails_task| 60s                                 |

> `check_overdue_reminders` and `check_sla_breach_warnings` exist in code but are **not** scheduled.

### Queue routing (`main/celery.py` task_routes — 6 globs + default)

```
apps.billing.tasks.*                              → kanzan_webhooks    (dormant — apps/billing/tasks.py does not exist)
apps.notifications.tasks.send_email_*             → kanzan_email
apps.notifications.tasks.send_notification_email  → kanzan_email
apps.inbound_email.tasks.*                        → kanzan_email
apps.tickets.tasks.send_ticket_*                  → kanzan_email
apps.api_keys.tasks.send_api_key_*                → kanzan_email
apps.voip.tasks.*                                 → kanzan_voip
*                                                 → kanzan_default
```

> **Caveat:** PM2 worker `-Q` list is `kanzan_default,kanzan_email,kanzan_webhooks` — `kanzan_voip` is NOT included. Add `kanzan_voip` to the worker args (or run a dedicated VoIP worker) when enabling VoIP tasks. `run_ari_listener` is NOT in PM2 by default.

## Management commands (7)

| Command                  | Purpose                                                              |
|--------------------------|----------------------------------------------------------------------|
| provision_tenant         | Create new tenant (`--name`, `--slug`, optional `--domain`)          |
| seed_plans               | Seed Free/Pro/Enterprise plans (idempotent)                          |
| setup_queues             | Seed 4 default queues per tenant                                     |
| setup_ticket_statuses    | Seed 5 default statuses per tenant                                   |
| backfill_sla_audit       | Baseline SLA audit rows for in-flight tickets (`--dry-run` supported) |
| run_smtp_server          | Long-running aiosmtpd server (kanzan-smtp PM2 process)               |
| run_ari_listener         | Long-running Asterisk ARI Stasis loop (NOT in PM2 by default)        |

## Key facts often missed

- `nav` app has **no `models.py` and no migrations folder** — it's purely the badge-counts API endpoint.
- `inbound_email` has `api_urls.py` (the others use `urls.py`); it is mounted at both `/api/v1/inbound-email/` and `/api/v1/emails/` (alias namespace `emails_api`).
- `crm.Reminder` has a custom `ReminderQuerySet` + `ReminderManager` and an `unscoped` manager. `status` is a **derived property**, not a stored column.
- `Reminder.contact` and `Reminder.ticket` (singular FKs) are gone since crm migration 0004 — use `reminder.contacts.add(c)` / `reminder.tickets.add(t)`.
- `TenantMembership.effective_role` should be used in place of `.role` whenever a request is being authorized — temporary-role overrides apply via this property. `temporary_permissions` M2M intersects with `temporary_role.permissions` to scope the grant.
- `InboundEmail` extends `TimestampedModel` (not `TenantScopedModel`) so its `tenant` FK is nullable until parsing resolves a tenant. Saving its idempotency key + `linked_at`/`actioned_at` immutability is enforced in the model's `save()`.
- **Seven system roles**, not four: `admin` (10), `manager` (20), `team-lead` (25), `agent` (30), `it` (30), `hr` (30), `viewer` (40). Backfilled by data migration `accounts/0011_seed_team_lead_it_hr_roles`. Viewer is permission-less by design — relies on the ≤40 view fallback in `HasTenantPermission`.
- **API keys** mint a hidden synthetic `User` with `is_service_account=True` as their `service_user`. UI must filter these out of staff lists.
- **Kanban drags trigger the full ticket service** when the target column has a different status — audit log, ticket-feed broadcast, SLA pause handling all fire as if the user changed status from the ticket form.
- **`Ticket.save()` auto-fills `company` from a linked Contact** when no company is set explicitly. Never overwrites.
- **`Article.save()` resolves tenant from `get_current_tenant()`** before its slug-uniqueness scan, and falls back to `"article"` if `slugify(title)` is empty — fix for DRF-created articles arriving with `tenant_id=None`.
