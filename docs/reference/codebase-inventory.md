# Codebase Inventory — Verified 2026-05-11

> Verified against `main @ bb36325`. Source of truth for **what models, migrations, signals, tasks, and management commands exist**.

## Apps directory (20 apps under `apps/`)

| App              | Models | Latest migration                                                          | Tasks | Signals | Notes                                                                |
|------------------|--------|---------------------------------------------------------------------------|-------|---------|----------------------------------------------------------------------|
| accounts         | 7      | `0007_add_temporary_role`                                                 | 0     | 1       | User+Permission+Role+Profile+TenantMembership+Invitation+EmailVerificationToken |
| agents           | 1      | `0005_agentavailability_auto_away_outside_hours_and_more`                 | 0     | 0       | AgentAvailability + working_hours JSON + auto_away_outside_hours bool |
| analytics        | 4      | `0003_calendarevent_color_calendarevent_end_date_and_more`                | 1     | 0       | ReportDefinition+DashboardWidget+ExportJob+CalendarEvent             |
| attachments      | 1      | `0001_initial`                                                            | 0     | 0       | Attachment (polymorphic GenericFK)                                   |
| billing          | 4      | `0002_plan_has_call_recording_plan_has_voip_and_more`                     | 0     | 0       | Plan with VoIP feature flags                                         |
| comments         | 4      | `0008_alter_activitylog_action`                                           | 0     | 0       | ActivityLog has 23 action choices                                    |
| contacts         | 5      | `0004_contact_lead_score`                                                 | 0     | 0       | Adds Contact.lead_score (0–100)                                      |
| crm              | 2      | `0004_reminder_m2m_contacts_tickets`                                      | 3     | 0       | **Reminder M2M migration is the gotcha** — `contact`/`ticket` FKs gone |
| custom_fields    | 2      | `0001_initial`                                                            | 0     | 1       | EAV; signals sync from `custom_data` JSON                            |
| inbound_email    | 3      | `0008_alter_inboundemail_recipient_email_imappollstate`                   | 2     | 0       | IMAPPollState (uid_validity + last_uid watermark)                    |
| kanban           | 3      | `0003_alter_cardposition_tenant`                                          | 0     | 0       | Polymorphic CardPosition                                             |
| knowledge        | 6      | `0004_kbrevision_kbsearchgap_kbticketlink_kbvote_and_more`                | 2     | 1       | Article + revision + votes + search gaps                             |
| messaging        | 3      | `0001_initial`                                                            | 0     | 0       | Conversation + Participant + Message                                 |
| nav              | 0      | (no migrations folder)                                                    | 0     | 0       | URL-only stub — `/api/v1/nav/badge-counts/` only                     |
| newsfeed         | 3      | `0002_reactions_reads_enhancements`                                       | 0     | 0       | NewsPost + Reaction + Read                                           |
| notes            | 1      | `0001_initial`                                                            | 0     | 0       | QuickNote — 6 colors                                                 |
| notifications    | 2      | `0004_rename_recall_to_reminder`                                          | 2     | 0       | Notification + NotificationPreference                                |
| tenants          | 2      | `0008_tenantsettings_auto_assign_inbound_email_tickets`                   | 0     | 1       | Tenant + TenantSettings + auto-assign toggle                         |
| tickets          | 22     | `0026_alter_ticketactivity_event`                                         | 10    | 1       | TicketActivity has 27 event choices                                  |
| voip             | 5      | `0002_voipsettings_asterisk_use_ssl_and_more`                             | 3     | 1       | Asterisk ARI integration                                             |

**Totals:** 20 apps · **80 model classes** (excludes `TextChoices` / `Manager` / `QuerySet` definitions) · 23 Celery tasks · 6 signal modules.

## Detailed model breakdown

### accounts (7)
`User`, `Permission`, `Role`, `Profile`, `TenantMembership`, `Invitation`, `EmailVerificationToken`

### agents (1)
`AgentAvailability` (TenantScopedModel) — status enum + `max_concurrent_tickets` + `current_ticket_count` + `working_hours` JSON + `auto_away_outside_hours`. Plus `AgentStatus` (TextChoices).

### analytics (4)
`ReportDefinition`, `DashboardWidget`, `ExportJob`, `CalendarEvent`

### attachments (1)
`Attachment` (polymorphic GenericFK; tenant-scoped path)

### billing (4)
`Plan` (Free/Pro/Enterprise + VoIP flags), `Subscription` (1:1 Tenant), `Invoice`, `UsageTracker`

### comments (4)
`Comment` (polymorphic, threaded, `is_internal`), `Mention`, `CommentRead`, `ActivityLog` (23 action choices, polymorphic audit trail)

### contacts (5)
`Account`, `Company`, `Contact`, `ContactGroup`, `ContactEvent`

### crm (2)
`Activity`, `Reminder` (M2M `contacts` + `tickets` since migration 0004; status is a derived property; has `unscoped` manager)

### custom_fields (2)
`CustomFieldDefinition`, `CustomFieldValue` (EAV)

### inbound_email (3)
`InboundEmail` (TimestampedModel — NOT TenantScopedModel; tenant nullable), `BounceLog`, `IMAPPollState`

### kanban (3)
`Board`, `Column`, `CardPosition` (polymorphic GenericFK)

### knowledge (6)
`Category`, `Article`, `KBRevision`, `KBVote`, `KBSearchGap`, `KBTicketLink`

### messaging (3)
`Conversation`, `ConversationParticipant`, `Message`

### newsfeed (3)
`NewsPost`, `NewsPostReaction`, `NewsPostRead` (NewsPostRead is NOT tenant-scoped)

### notes (1)
`QuickNote` (6 colors, pinning)

### notifications (2)
`Notification` (15 types), `NotificationPreference`

### tenants (2)
`Tenant`, `TenantSettings`

### tickets (22)
`Pipeline`, `PipelineStage`, `TicketStatus`, `Queue`, `TicketCategory`, `TicketCounter`, `Ticket`, `TicketLink`, `SLAPolicy`, `EscalationRule`, `BusinessHours`, `PublicHoliday`, `SLAPause`, `TicketActivity` (27 event choices), `CannedResponse`, `Macro`, `SavedView`, `TicketAssignment`, `TicketWatcher`, `TimeEntry`, `TicketTemplate`, `Webhook`

### voip (5)
`VoIPSettings`, `Extension`, `CallLog`, `CallRecording`, `CallQueue`

## Signals (6 modules)

| App           | File                              | Notes                                                                                               |
|---------------|-----------------------------------|-----------------------------------------------------------------------------------------------------|
| tenants       | `apps/tenants/signals.py`         | `Tenant.post_save` → create_tenant_settings + create_default_roles                                  |
| accounts      | `apps/accounts/signals.py`        | `TenantMembership.post_save` → create_profile_on_membership                                         |
| tickets       | `apps/tickets/signals.py`         | 8+ handlers: status changes, activity logging, kanban sync, SLA pause/resume, KB coverage, webhooks |
| custom_fields | `apps/custom_fields/signals.py`   | `Ticket.post_save` / `Contact.post_save` → sync `CustomFieldValue` rows from `custom_data` JSON     |
| knowledge     | `apps/knowledge/signals.py`       | Article review status transitions; `Article.post_save` → update Postgres `search_vector`            |
| voip          | `apps/voip/signals.py`            | `CallLog.post_save` on terminal status → TicketActivity + ActivityLog + queue recording task        |

## Celery tasks (23 across 7 apps)

| App           | Tasks (registered name in parens if different)                                                   |
|---------------|--------------------------------------------------------------------------------------------------|
| tickets       | `check_sla_breaches`, `check_overdue_tickets`, `check_sla_breach_warnings`, `propagate_sla_policy_change_task`, `send_ticket_reply_email_task`, `send_ticket_created_email_task`, `send_ticket_email_task`, `auto_close_ticket`, `send_csat_survey_email`, `deliver_webhook_task` |
| notifications | `send_notification_email`, `cleanup_old_notifications`                                           |
| inbound_email | `fetch_inbound_emails_task`, `process_inbound_email_task`                                        |
| knowledge     | `alert_stale_articles` (`knowledge_base.alert_stale_articles`), `send_gap_digest` (`knowledge_base.send_gap_digest`) |
| crm           | `check_overdue_reminders`, `calculate_lead_scores`, `calculate_account_health_scores`            |
| voip          | `process_call_recording`, `cleanup_stale_calls`, `sync_call_state`                               |
| analytics     | `process_export_job`                                                                             |

### Beat schedule (`main/settings/base.py` `CELERY_BEAT_SCHEDULE`, 9 entries)

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

`check_overdue_reminders` exists in code but is **not** scheduled.

### Queue routing (`main/celery.py` task_routes)

```
apps.billing.tasks.*                              → kanzan_webhooks
apps.notifications.tasks.send_email_*             → kanzan_email
apps.notifications.tasks.send_notification_email  → kanzan_email
apps.inbound_email.tasks.*                        → kanzan_email
apps.tickets.tasks.send_ticket_*                  → kanzan_email
apps.voip.tasks.*                                 → kanzan_voip
*                                                 → kanzan_default
```

> **Caveat:** PM2 worker `-Q` list is `kanzan_default,kanzan_email,kanzan_webhooks` — `kanzan_voip` is NOT included. Add `kanzan_voip` to the worker args (or run a dedicated VoIP worker) when enabling VoIP tasks.

## Management commands (8)

| Command                  | Purpose                                                              |
|--------------------------|----------------------------------------------------------------------|
| provision_tenant         | Create new tenant (`--name`, `--slug`, optional `--domain`)          |
| seed_plans               | Seed Free/Pro/Enterprise plans                                       |
| setup_queues             | Seed 4 default queues per tenant                                     |
| setup_ticket_statuses    | Seed 5 default statuses per tenant                                   |
| backfill_sla_audit       | Baseline SLA audit rows for in-flight tickets                        |
| run_smtp_server          | Long-running aiosmtpd server (kanzan-smtp PM2 process)               |
| run_ari_listener         | Long-running Asterisk ARI Stasis loop (NOT in PM2 by default)        |
| (per-app default `*startapp` etc.)                                                              |

## Key facts often missed

- `nav` app has **no `models.py` and no migrations folder** — it's purely the badge-counts API endpoint.
- `inbound_email` has `api_urls.py` (the others use `urls.py`); it is mounted at both `/api/v1/inbound-email/` and `/api/v1/emails/` (alias namespace `emails_api`).
- `crm.Reminder` has a custom `ReminderQuerySet` + `ReminderManager` and an `unscoped` manager. `status` is a **derived property**, not a stored column.
- `Reminder.contact` and `Reminder.ticket` (singular FKs) are gone since crm migration 0004 — use `reminder.contacts.add(c)` / `reminder.tickets.add(t)`.
- `TenantMembership.effective_role` should be used in place of `.role` whenever a request is being authorized — temporary-role overrides apply via this property.
- `InboundEmail` extends `TimestampedModel` (not `TenantScopedModel`) so its `tenant` FK is nullable until parsing resolves a tenant. Saving its idempotency key + `linked_at`/`actioned_at` immutability is enforced in the model's `save()`.
