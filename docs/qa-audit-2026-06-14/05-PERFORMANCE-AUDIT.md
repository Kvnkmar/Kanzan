# Kanzen — Performance & Database Audit
**Date:** 2026-06-14 · DB: SQLite (dev) / PostgreSQL (prod) · Redis cache/sessions/Channels · Celery (12 beat tasks)

> Code-level audit only — **no load/profiling run** was performed. Numbers/impact are reasoned from query shapes and indexes; validate with `django-silk`/`EXPLAIN`/load tests before launch.

## Summary
Indexing is generally thoughtful (composite + partial indexes on hot paths, `db_index` on FKs). The main risks are **(1) a partial-index/task-scan mismatch causing full scans on a 120 s beat task, (2) classic N+1s in a few list serializers, (3) a stale-cache correctness/perf issue, and (4) several SQLite-only no-ops that hide prod behavior in dev.**

## High-severity

### PERF-H1 · HubEmail SLA partial index excludes `ESCALATED` but the task scans it `[inbox-hub-access-4 / performance-db-2]`
- **Location:** `apps/inbox_hub/models.py:240-244` (index condition) vs `apps/inbox_hub/tasks.py:32-37` (active states).
- **Issue:** Partial index `ih_email_active_sla_due` filters `state in (new, assigned, in_progress, pending_agent)`. `check_hub_sla_breaches` (beat **120 s**) scans those **plus `escalated`**. The planner cannot use the index for `ESCALATED` rows → **full table scan** every 2 minutes as escalations accumulate.
- **Fix:** Add `'escalated'` to the index `condition` (one-line migration). Verify with `EXPLAIN` on Postgres.

## Medium-severity (verified-adjacent / catalogued)

| ID | Issue | Location | Fix |
|---|---|---|---|
| `contacts-crm-2` | `contact_context_v2` cache never invalidated on ticket/contact mutation (stale **and** wasteful 60 s recompute churn) | `apps/contacts/context.py:23-119` | Invalidate on `Ticket`/`Contact` signals |
| `performance-db-8` | `InboxViewSet.get_queryset()` missing `select_related`/`prefetch` → N+1 over related fields | `apps/inbound_email/api_views.py:354-365` | Add `select_related`/`prefetch_related` |
| `performance-db-11` | `DashboardWidget` serialization missing `select_related` → N+1 | `apps/analytics/views.py:99-100` | `select_related('user', …)` |
| `data-model-integrity-6` / `performance-db-*` | `InboundEmail.save()` does a **fresh SELECT on every update** to enforce field immutability | `apps/inbound_email/models.py:242-257` | Cache original on load, or guard via `update_fields` |
| `custom-fields-agents-6`, `performance-db-12`, `settings-secrets-12` | Feature A `due_notified_at` re-arm predicate (`due_notified_at < F('scheduled_at')`) is **unindexed** → sequential filter each 30 s beat | `apps/crm/models.py:156-208`, `apps/crm/tasks.py:70-84` | Acceptable now (narrow set via existing `reminder_overdue_idx`); add a composite/partial index if reminder volume grows |
| `feature-a-reminder-7` | `prefetch_related` combined with `.iterator(chunk_size=)` issues a query per chunk (prefetch defeated) | `apps/crm/tasks.py:82-83` | Drop `.iterator()` or pre-resolve contacts |
| `performance-db-14` | `Reminder.contacts` prefetched even when unused in the fire path → over-fetch | `apps/crm/tasks.py:81-83,106` | Prefetch only when needed |

## Signal-bypassing writes (correctness + observability)
`.update()`-based writes intentionally skip `post_save` to avoid signal churn, but this makes them **invisible to the live broadcast layer** and to any cache invalidation:
- `Contact.last_activity_at`, lead/health-score recalcs, `ContactEvent` recording.
- The reminder claim (`due_notified_at`) — correct here (avoids re-tripping the signal), but the contact/score cases mean the UI won't reflect changes in real time.

**Recommendation:** where real-time reflection matters, emit an explicit `broadcast_live_event` after the bulk update, or invalidate the relevant cache key.

## SQLite-only no-ops that mask prod behavior
These work differently (or not at all) in dev vs prod — a testing hazard:
- **KB full-text search** silently returns nothing on SQLite (`knowledge-1`, see Bug Report) — no fallback.
- **`TicketCounter.next_number()`** `SELECT ... FOR UPDATE` + F-expression is a **no-op on SQLite** — concurrency correctness only exercised on Postgres.
- **`Article.update_search_vector`** early-returns on non-Postgres.

**Recommendation:** run the suite (or a smoke subset) against Postgres in CI so these paths are actually exercised.

## Concurrency / locking
- `merge_tickets`/`split_ticket` **release their `select_for_update` lock immediately** after `.exists()` (`tickets-services-sla-1`, High — see Bug Report) — both a correctness and a contention concern.
- Auto-assign / WIP-limit checks and `fire_due_reminders` lack `select_for_update` on the decisive read → over-allocation / lost-update windows (single-beat scheduling currently masks the reminder case).

## Operational
- **`logs/` = 95 MB, no rotation** (gitignored) — disk-growth risk; add logrotate/PM2 log rotation.
- **`kanzan_voip` Celery queue has no worker** — `cleanup-stale-calls` (hourly) and other VoIP tasks **pile up unconsumed** in the broker.
- **`CELERY_TIMEZONE="UTC"`** while `TIME_ZONE="Asia/Kuala_Lumpur"` — crontab beat entries (KB digests) fire on UTC wall-clock; confirm intended.
- `process_export_job.delay()` not wrapped in `transaction.on_commit` — can run before the row commits and strand the job at `pending`.

## What looks healthy
- Composite/partial indexes on tickets, hub emails, inbound email, contacts.
- Pagination (`PAGE_SIZE=50`) and scoped throttles (`api_default 200/min`, `api_heavy 30/min`).
- `select_related`/`prefetch_related` present on the heavy `HubEmailViewSet` and ticket querysets.
