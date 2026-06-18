# Kanzen — Remediation Plan (Phase 2)
**Date:** 2026-06-14 · Prioritized fix plan. **No code changes yet** — Phase 3 implementation awaits approval.

Effort key: **S** ≤1h · **M** ≤½ day · **L** 1–2 days. Each item lists the fix and a verification step.

---

## 🔴 SPRINT 0 — Launch blockers (do first; ~2–3 days)

| # | ID | Fix | Effort | Verify |
|---|---|---|---|---|
| 1 | `settings-secrets-1` | In `main/settings/__init__.py:9` change `default=True` → `default=False`; add a startup assertion that `DEBUG is False` when scheme is https. | S | Unset `DJANGO_DEBUG` → confirm prod settings load; add a test asserting `__init__` defaults to prod. |
| 2 | `messaging-1/2/3` | Tenant-scope user resolution: `mentions.py:74`, `views.py:398`, `consumers.py:317` → filter `tenantmembership__tenant=tenant, is_active=True`; reject non-member `user_ids` in DM (`serializers.py:337-346`) and group (`:482-503`). | M | New tests: mention/DM/group with a foreign-tenant UUID → rejected / no notification. |
| 3 | `attachments-1/2/3` | Add object-level authz: a `HasTargetObjectPermission` reusing `agent_visible_tickets_q` (tickets), comment/message access; enforce in `AttachmentUploadSerializer.validate()` and `check_object_permissions`; serve files via an authz'd download view (`FileResponse`/`X-Accel-Redirect`) instead of bare `/media/`. | L | Tests: agent uploads/reads/downloads another agent's ticket attachment → 403. |
| 4 | `billing-1` | Add `has_voip`/`has_call_recording`/`max_calls_per_month` to each plan dict in `seed_plans.py`; data migration to backfill existing `Plan` rows. | S | Re-seed → Enterprise `has_voip=True`; `check_call_limit` allows a call. |
| 5 | `billing-2` | Make the Stripe webhook idempotent per tenant: key `update_or_create` on `tenant` (overwrite `stripe_subscription_id`) **or** delete the old row in the `.deleted` handler; consider `ForeignKey`+`is_active` if multiple historical subs are wanted. | M | Test: cancel → re-subscribe webhook sequence → no IntegrityError, one active sub. |
| 6 | `inbound-email-1`/`tenant-isolation-3` | Resolve tenant before the IMAP dedup and filter `(tenant, message_id)` at `imap_poller.py:337`. Plan the larger refactor of `InboundEmail` → `TenantScopedModel` (non-null tenant) as a follow-up. | M (quick) / L (refactor) | Test: two tenants, same Message-ID → both ingest. |
| 7 | **Test suite** | Re-green outbound-email tests (use a deliverable domain or test-only allowlist), update the 2 badge + 1 comment-visibility stale tests. | M | `pytest` exit 0. |
| 8 | **CI** | Add a CI pipeline running `make check` (ruff + migrate-check + full pytest) **against PostgreSQL**; block merge on red. | M | PR triggers CI; red blocks. |

**Exit criteria for Sprint 0:** all 6 Criticals fixed + suite green + CI live. Re-score target ≥ 60.

---

## 🟠 SPRINT 1 — High-priority hardening (~3–5 days)

| # | ID | Fix | Effort |
|---|---|---|---|
| 9 | `authz-rbac-2`, `tickets-core-7` | Replace `role.hierarchy_level` → `effective_role.hierarchy_level` across all ~20 sites (`agents/services.py:226`, `analytics/services.py:52`, `kanban/serializers.py:188`, `tickets/views.py:576,1386`, …). Add a grep-based lint guard + an expired-temp-role cleanup task. | L |
| 10 | `data-model-integrity-1`, `tickets-core-1/2` | Add `validate_assignee` (tenant-member) + `validate_contact/company/pipeline_stage` where the FK isn't tenant-scoped; call `full_clean()` in ticket create/update. | M |
| 11 | `inbox-hub-access-3`, `inbox-hub-engine-2` | Decide SLA semantics: stamp `first_responded_at` on first agent reply **and** add an `escalation_breached` one-shot; re-fetch `response_breached` from DB in the task. | M |
| 12 | `authn-2` | Add `HasTenantPermission` + an `analytics.view` codename to `DashboardView`. | S |
| 13 | `authn-3` | Add `Invitation.consumed_at`; consume atomically + enforce email match in `accept_invitation`. | M |
| 14 | `websockets-1` | Scope VoIP `CallEventConsumer` to extension owners/admins; filter payload by role. | L |
| 15 | `websockets-3` | Enforce `agent_can_see_ticket` in `TicketPresenceConsumer._can_access_ticket`. | S |
| 16 | comments LiveBus leak | Omit internal-comment bodies from the tenant-wide `live_tenant_*` broadcast (or fan out only to authorized recipients). | M |
| 17 | `custom-fields-agents-3` | Enforce `visible_to_roles` in `CustomFieldDefinitionViewSet.get_queryset()` + writes. | M |
| 18 | `tickets-services-sla-1` | Keep the merge/split lock alive (materialize queryset / re-fetch under `select_for_update`). | S |
| 19 | `tickets-signals-2` | Add `Column.pipeline_stage` FK; match on it instead of column name (+ warn on fallback). | M |
| 20 | `custom-fields-agents-1` | Add `Company.post_save` custom-field sync receiver. | S |
| 21 | `custom-fields-agents-4` | Evaluate agent working-hours in tenant/agent timezone; fail-open when missing. | M |
| 22 | `messaging-4` | `validate_body` rejecting empty-after-strip (allow empty only with attachments). | S |
| 23 | `knowledge-1` | Implement the documented SQLite `icontains` FTS fallback. | S |
| 24 | `contacts-crm-2` | Invalidate `contact_context_v2` on ticket/contact mutation. | S |
| 25 | `frontend-js-2` | Unify WebSocket reconnect on infinite-with-jitter; surface per-channel dead state. | M |
| 26 | `frontend-js-3` | Widen `ConvertToTicketSerializer` + Hub viewset to accept the full Feature B override set. | S |
| 27 | `inbox-hub-access-4`/`performance-db-2` | Add `'escalated'` to the HubEmail partial-index condition. | S |
| 28 | `data-model-integrity-4`, `feature-a-reminder-5` | Re-validate/lock reminders after claim; handle deleted-recipient with a warning/fallback. | M |

---

## 🟡 SPRINT 2 — Medium (correctness, perf, a11y) (~1 week)
- **Accessibility:** `templates-uiux-2/3/6/8/9` — keyboard-accessible password toggle, `aria-label`s on icon buttons, focus restore on modal close, `role="alertdialog"`/`aria-live` for the reminder modal. Audit `custom-select.js`/command-palette/keyboard-shortcuts widgets for ARIA + focus trapping.
- **Notification email link safety:** `notifications-9` — validate/sanitize `data.url` (relative or allowlisted) before rendering in email templates.
- **Performance:** add `select_related`/`prefetch` to `InboxViewSet` and `DashboardWidget`; fix the `prefetch + iterator` anti-pattern in `fire_due_reminders`; reconsider `InboundEmail.save()` per-update SELECT.
- **Data model:** `data-model-integrity-2` — change `NewsPost.author` CASCADE → SET_NULL/PROTECT (prevent silent post loss).
- **Ops:** add log rotation (95 MB unrotated); add a worker for `kanzan_voip` (or remove the queue/tasks); wrap `process_export_job.delay` in `on_commit`; confirm `CELERY_TIMEZONE` intent; fix export PDF/XLSX filename↔content mismatch.
- **Dead code:** fix command-palette `/contacts/new/`; delete `kb_sidebar_widget.html`; remove dead macro JS in `tickets/detail.html`; decide on `require_feature`, `KBRevision`/`KBTicketLink`, `PARKED_IN_HUB`.
- **Coverage:** add direct tests for `create_ticket` action + `_build_ticket_overrides`, billing webhooks, WebSocket auth, attachment authz/MIME.

## ⚪ SPRINT 3 — Low (hygiene)
- Resolve the 196 ruff issues (`ruff --fix` for the 155 auto-fixable; manually review `F601` repeated key, `F841` in app code, `F811`).
- Add `pytest-timeout` to `dev.txt`; set `asyncio_mode` in `pytest.ini`.
- Doc drift: activity dedup "5s"→"2s"; KB vote endpoint schema; stale `docs/reference/*`.
- Tidy `make logs-django` (`.PHONY` w/o body), `make stop/restart` omitting `kanzan-smtp`.

---

## Suggested sequencing & validation discipline
1. **Branch per sprint**; never commit to `main` directly.
2. For each fix: write/adjust a failing test first, implement, run the targeted test, then `make check`.
3. Run the **full suite against PostgreSQL** at the end of each sprint (catches SQLite-masked bugs).
4. Re-run this audit's quality gates (`ruff`, `makemigrations --check`, `theme-check`, `pytest`) before each PR.
5. Re-score readiness after Sprint 0 and Sprint 1.

> **Order of attack:** Sprint 0 #1 (DEBUG) and #7/#8 (green tests + CI) first — they're cheap and de-risk everything else. Then the cross-tenant trio (#2, #3, #6), then billing (#4, #5).
