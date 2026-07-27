# CRM — Bug Report
**Date:** 2026-06-14 · Functional / data-integrity / reliability defects (security defects are in `03-SECURITY-ASSESSMENT.md`).

Each entry: **Severity · Location · Description · Steps · Expected · Actual · Fix**. IDs map to `_digest_confirmed.md`. All Critical/High below were adversarially verified; the headline Criticals were also hand-verified.

---

## 🔴 CRITICAL

### BUG-C1 · Billing: VoIP entitlement is OFF for every plan `[billing-1]`
- **Location:** `apps/billing/management/commands/seed_plans.py:20-90`
- **Description:** The seeder defines Free/Pro/Enterprise but omits `has_voip`, `has_call_recording`, `max_calls_per_month`, so all default to `False/False/NULL`. `voip/services.py::check_call_limit` denies calls when `not plan.has_voip`.
- **Steps:** `python manage.py seed_plans` → `SELECT has_voip FROM billing_plan` → all `False` → attempt a call via `InitiateCallView` → denied "VoIP not available on plan."
- **Expected:** Pro/Enterprise have `has_voip=True` with sensible `max_calls_per_month`.
- **Actual:** VoIP is unusable for every tenant, including Enterprise.
- **Fix:** Add the three keys to each plan dict in the seeder; backfill existing `Plan` rows via a data migration.

### BUG-C2 · Billing: second Subscription per tenant raises IntegrityError `[billing-2]`
- **Location:** `apps/billing/models.py:107-111` (`OneToOneField`), `apps/billing/webhooks.py:135-138`
- **Description:** `Subscription.tenant` is a `OneToOneField`. The Stripe webhook does `update_or_create(stripe_subscription_id=…)`. A **new** subscription id for a tenant that already has a (canceled) row violates the unique `tenant_id` constraint.
- **Steps:** Tenant has `sub_abc` (later canceled, row not deleted) → customer re-subscribes → Stripe sends `customer.subscription.created` for `sub_def` → handler inserts with `tenant=t1` → **IntegrityError**.
- **Expected:** Re-subscribe/second subscription replaces cleanly.
- **Actual:** Webhook 500s; subscription state desyncs.
- **Nuance (verified):** In-place plan *changes* keep the same `stripe_subscription_id` (update path) and are **unaffected**. The break is on re-subscribe after cancellation or any second subscription.
- **Fix:** Either key `update_or_create` on `tenant` (and overwrite `stripe_subscription_id`), or change to `ForeignKey` + an `is_active` selector, or hard-delete the old row in the `.deleted` handler.

### BUG-C3 · Inbound email: cross-tenant Message-ID dedup drops mail `[inbound-email-1 / tenant-isolation-3]`
- **Location:** `apps/inbound_email/imap_poller.py:337`; `apps/inbound_email/models.py:22-75`
- **Description:** `InboundEmail` is **not** a `TenantScopedModel` (plain manager, nullable tenant). The IMAP dedup `InboundEmail.objects.filter(message_id=message_id).exists()` has **no tenant filter**, so the same Message-ID arriving for two tenants is treated as a duplicate for the second.
- **Steps:** Tenant A and Tenant B both receive a mailing-list email with identical `Message-ID` → A ingests it → B's copy hits line 337, matches A's row, is skipped & marked seen → **B never gets a ticket**.
- **Expected:** Dedup scoped per tenant (or per unresolved-tenant batch).
- **Actual:** Cross-tenant collision → silent email loss / potential misrouting.
- **Fix:** Resolve tenant before the dedup check and filter by `(tenant, message_id)`; longer-term make `InboundEmail` a `TenantScopedModel` (non-null tenant) so all queries are fail-closed.

---

## 🟠 HIGH

### BUG-H1 · Inbox Hub: response SLA always breaches & auto-escalates `[inbox-hub-access-3 / inbox-hub-engine-1/2]`
- **Location:** `apps/inbox_hub/tasks.py:49-57`; field `apps/inbox_hub/models.py:183`
- **Description:** The breach guard tests `first_responded_at is None`, but **no code ever writes `first_responded_at`**. So every active email breaches its response deadline and `check_hub_sla_breaches` auto-escalates it (incrementing `escalation_count`), regardless of whether an agent replied.
- **Steps:** Create HubEmail with response deadline → agent replies → deadline passes → task flags `response_breached=True` and escalates anyway.
- **Expected:** A recorded agent response marks SLA met and suppresses the breach.
- **Actual:** Breach + escalation always fire on deadline. `escalation_count` can double-increment under stale-object/concurrent runs (no escalation dedup).
- **Fix:** Stamp `first_responded_at` when an agent first replies; re-fetch `response_breached` from DB in the task; add an `escalation_breached` one-shot flag.

### BUG-H2 · Tickets: pipeline-stage→kanban column sync matches by NAME `[tickets-signals-2 / kanban-3]`
- **Location:** `apps/tickets/signals.py:618`
- **Description:** Stage→column sync filters `Column.name__iexact=stage_name`. Renaming a column silently breaks the mapping; cards never move and nothing is logged.
- **Steps:** Card in "Qualification" column → admin renames column to "Lead Qualification" → ticket returns to that stage → sync can't find the column → no-op, no warning.
- **Expected:** Mapping survives renames (FK), or a warning is logged.
- **Actual:** Silent breakage; cards stranded.
- **Fix:** Add a `pipeline_stage` FK on `Column` and match on it; at minimum log a WARNING on name-match failure.

### BUG-H3 · Tickets: merge/split release their lock immediately `[tickets-services-sla-1 / tickets-signals-3]`
- **Location:** `apps/tickets/services.py:1516-1518` (merge), `:1660` (split)
- **Description:** Code calls `select_for_update().filter(...).exists()` and discards the result — `.exists()` closes the cursor, releasing the row lock **before** the merge/split data movement. The docstring's "locked for the duration of the transaction" is false.
- **Steps:** Start merge in worker 1; concurrently modify a participating ticket in worker 2 → not blocked → interleaved writes corrupt the merge.
- **Expected:** Rows locked across the whole transaction.
- **Actual:** Lock released after `.exists()`; concurrent writes slip in.
- **Fix:** Materialize the locked queryset (`list(...select_for_update()...)`) or re-fetch under `select_for_update()` immediately before each mutation.

### BUG-H4 · Knowledge base: full-text search returns nothing on SQLite `[knowledge-1]`
- **Location:** `apps/knowledge/search.py:24-28`, `models.py:120`, `signals.py:18-19`
- **Description:** `kb_search` uses Postgres-only `SearchQuery`/`SearchRank`. On SQLite (dev) this yields an empty result set; the `icontains` fallback the API docstring claims **does not exist**.
- **Steps:** Dev (SQLite) → publish an article → `GET /api/v1/knowledge/search/?q=term` → empty results despite a match.
- **Expected:** FTS on Postgres, `icontains` fallback on SQLite (as documented).
- **Actual:** Zero results in dev; behavior silently differs from docs.
- **Fix:** Add the engine-detected `icontains` fallback (mirror `signals.py:17-19`), or correct the documentation.

### BUG-H5 · CRM reminders: backward reschedule never fires again `[data-model-integrity-4]`
- **Location:** `apps/crm/tasks.py:70-104`
- **Description:** `fire_due_reminders` SELECTs then UPDATEs `due_notified_at` with no `select_for_update`. If a reminder is rescheduled **backward** between SELECT and UPDATE, the watermark ends up `>= scheduled_at`, permanently disarming the re-fire guard.
- **Steps:** Reschedule a reminder earlier in the window between the task's SELECT and claim-UPDATE → `due_notified_at > new scheduled_at` → reminder never fires.
- **Expected:** Reminder fires when `scheduled_at <= now`.
- **Actual:** Lost forever for that race.
- **Fix:** Re-validate the reminder after claiming, or lock with `select_for_update`.

### BUG-H6 · CRM reminders: recipient deleted mid-flight swallows the alert `[feature-a-reminder-5]`
- **Location:** `apps/crm/tasks.py:86-117`
- **Description:** Claim-first stamps `due_notified_at` before `send_notification`. If the recipient user is deleted between fetch and send, `Notification.save()` raises `IntegrityError`, which is caught/logged — leaving the reminder marked notified but **no alert delivered and no admin escalation**.
- **Fix:** Validate recipient existence / catch `IntegrityError` explicitly with a high-severity log, or fall back to a tenant admin.

### BUG-H7 · Messaging: blank message bodies persist `[messaging-4]`
- **Location:** `apps/messaging/models.py:133` (required) vs `serializers.py:127` (`allow_blank=True`)
- **Description:** Model requires `body` (no `blank=True`) but `MessageCreateSerializer` allows blank/empty; `save()` skips `full_clean()`, so empty rows persist.
- **Steps:** `POST .../messages/ {body:'', parent:null}` → empty message stored.
- **Fix:** `validate_body` rejecting empty-after-strip when there are no attachments.

### BUG-H8 · Custom fields: Company custom fields never indexed `[custom-fields-agents-1]`
- **Location:** `apps/custom_fields/signals.py:20-43`, `apps/contacts/signals.py`
- **Description:** Sync signals exist for Ticket & Contact but **not Company**, despite `Company.custom_data` and `ModuleType.COMPANY` existing. Company custom-field values are never written to `CustomFieldValue` → unsearchable/unfilterable.
- **Steps:** Define a `module='company'` field → PATCH a company's `custom_data` → query `CustomFieldValue` → 0 rows.
- **Fix:** Add a `Company.post_save` receiver calling `sync_custom_field_values(instance, module='company')`.

### BUG-H9 · Contact-context cache never invalidated `[contacts-crm-2 / performance-db-4]`
- **Location:** `apps/contacts/context.py:23-119`; no invalidation in `apps/tickets/signals.py`
- **Description:** The `contact_context_v2` cache (60 s) feeds the ticket-detail sidebar and Hub context card. No ticket/contact mutation clears it, so `open_tickets`, CSAT, recent-tickets show stale data for up to 60 s.
- **Fix:** On `Ticket` post_save/post_delete (when `contact_id` or `status.is_closed` changes) and on `Contact` save, `cache.delete(f'contact_context_v2:{tenant}:{contact}')`.

### BUG-H10 · Feature B override set unreachable via Hub convert `[frontend-js-3]`
- **Location:** `apps/inbox_hub/services.py::convert_to_ticket` (widened) vs `apps/inbox_hub/serializers.py::ConvertToTicketSerializer` / viewset (not widened)
- **Description:** The service accepts `subject/description/category/due_date/tags`, but the Hub cockpit's convert serializer still forwards only `queue/status/assignee/priority`. The full override form only works from the Emails page.
- **Fix:** Widen `ConvertToTicketSerializer` + viewset to pass all overrides.

### BUG-H11 · WebSocket reconnect caps are inconsistent `[frontend-js-2]`
- **Location:** `static/js/app.js:690-722` (notifications, 10 attempts), `ticket-feed.js:19-108` (10 attempts), `live-connection.js:42-167` (infinite)
- **Description:** Notifications & ticket-feed give up after ~10 attempts; live retries forever. After a long outage, notifications/feed are **dead until manual reload** while the status pill still implies connectivity.
- **Fix:** Unify on infinite-with-jitter (or surface per-channel dead state).

### BUG-H12 · TicketPresence consumer doesn't check ticket visibility `[websockets-3]`
- **Location:** `apps/tickets/consumers.py:150-156` (`_can_access_ticket`)
- **Description:** Presence join only checks tenant membership, not `agent_can_see_ticket`. A Viewer or unrelated agent can join a ticket's presence group and see who's viewing it.
- **Fix:** Use `agent_can_see_ticket` / `effective_role <= 20` bypass in `_can_access_ticket`.

### BUG-H13 · Agent presence working-hours uses server timezone `[custom-fields-agents-4]`
- **Location:** `apps/agents/models.py:234-255`
- **Description:** `_within_working_hours` uses `timezone.localtime()` → server `TIME_ZONE` (Asia/Kuala_Lumpur), not the agent/tenant timezone. Cross-timezone tenants get wrong auto-away/assignable decisions.
- **Fix:** Evaluate working hours in the tenant/agent timezone; fail-open if tz missing.

---

## 🟡 MEDIUM (selected; full list in `_digest_med_low.md`)
| ID | Title | Location |
|---|---|---|
| `data-model-integrity-2` | NewsPost `author` CASCADE → deleting a user hard-deletes their posts (no audit trail) | `apps/newsfeed/models.py:29-33` |
| `frontend-js-1` | Command-palette "New Contact" → dead `/contacts/new/` (real: `/contacts/create/`) | `static/js/command-palette.js:28` |
| `tickets-signals-*` | Activity dedup window is 2 s but docstrings say 5 s; resolved/closed both set to `now` losing ordering | `apps/tickets/signals.py` |
| `inbound-email-*` | `settings` variable shadows `django.conf.settings` in `process_inbound_email`; `PARKED_IN_HUB` write-dead | `apps/inbound_email/services.py` |
| `knowledge-*` | `KBRevision`/`KBTicketLink` dead-write models; anonymous `KBVote` ballot-stuffing by `session_key` | `apps/knowledge/` |
| `analytics-*` | Export writes CSV bytes into `.pdf`/`.xlsx` filenames when libs absent; `process_export_job.delay` not in `on_commit` | `apps/analytics/` |
| `contacts/crm` | `last_activity_at`, score recalcs, ContactEvent use `.update()` → no `post_save` → invisible to live layer | `apps/contacts/`, `apps/crm/` |

> ~104 Medium and ~49 Low findings are catalogued in `_digest_med_low.md`. Mediums were not individually adversarially verified; treat confidence accordingly.
