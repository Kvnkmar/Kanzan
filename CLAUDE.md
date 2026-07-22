# Kanzen — Project Intelligence

> Last refreshed: **2026-07-10 (pass 12 — clean-room re-verification, docs detached; NEW security + go-live batch)** — fresh source-only pass with CLAUDE.md + MEMORY.md **detached**: my own git/DB/registry scouting + full `pytest`, then **7 parallel source-verification agents** (working-tree security fixes · go-live fixes · RBAC/tenancy · tickets/kanban/SLA · inbox-hub/email/notifications · infra/settings/celery · frontend/commit-boundary), each told to adversarially **CONFIRM / REFUTE / CORRECT** every prior claim against current source AND hunt new. **Verdict: the pass-11 doc holds ~99% and the CURRENT working-tree security+go-live batch is CORRECT and effectively COMPLETE (all 7 agents, 0 errors).** Verified against branch **`main`** @ HEAD **`df9b29d`** (`new updates`, Thu 2026-07-09) **== `origin/main`**.
>
> **⚠ TWO STATE SHIFTS since pass 11:**
> 1. **HEAD moved `6c6662d` → `df9b29d`.** Commit `df9b29d` **COMMITTED** the entire batch that passes 9–11 described as "uncommitted working tree": the Inbox-Hub SLA-response fix (migs `inbox_hub/0002`+`0003`), the Mobile Triage cockpit, the Triage-Lens removal, and all 5 UI passes — **so every pass-≤11 "uncommitted UI/SLA/mobile-triage" narrative below is now COMMITTED and byte-identical on disk** (custom-v15.css **25404**, base.html **318**, app.js **1288**, inbox-hub.js **1513** `?v=16` are the committed values).
> 2. **The CURRENT working tree is an entirely NEW security + go-live hardening batch (17 `M` + 5 `??`)** — see below. It touches ZERO frontend files.
>
> **Pass-12 gates (re-run 2026-07-10, all GREEN):** `pytest -q` = **1120 passed / 22 skipped / 1 xfailed** (1143 collected, 222s SQLite — +42 tests vs pass-11's 1078: `test_bulk_ticket_delete` [6] + `test_go_live_fixes` [9] + `test_rate_limiting` [8] + additions to `test_recalls`/`test_messaging_tenant_isolation`/`test_notifications`/`test_inbox_hub_routing_assignment`); `makemigrations --check` clean; `manage.py check` clean; `scripts/check_theme.py` OK (**137** vs baseline 145/10); `ruff check .` = **198** (non-blocking, pre-existing +1 from new files). Live DB re-run: **71 `accounts_permission` rows** vs 69 codenames (2 orphans `ticket_category.view`+`ticket_status.view`); **zero-perm role seeding STILL reproduced** (only `straat-x` correct: admin 69/manager 59/team-lead 40/agent 24/it 25/hr 25 — all 8 other tenants 0). Registry re-counts unchanged: 91 first-party models · 122 migrations · 46 INSTALLED_APPS · 14 middleware · 12 Beat · 27 tasks · 43 receivers · 76 admin · 8 commands · NotificationType 21 · INTERNAL_ONLY 6 · ActivityLog 34 · TicketActivity 27 · HubEmail 9/4 · ACTIVE_SLA_STATES 5 · derive_palette 24 · base.py 39 env keys · TenantMiddleware EXEMPT 17 · TicketViewSet 31 @actions · HubEmailViewSet 10.
>
> **⚠ CURRENT WORKING TREE (17 `M` + 5 `??`, uncommitted on `df9b29d`) — SECURITY + GO-LIVE batch (all verified correct, suite green):**
> **Security/data-integrity (5 fixes — each CLOSES a specific pass-10/11 finding):**
> 1. **Reminder `bulk_action` IDOR → FIXED** (closes pass-11 #1 + pass-10 #4). `crm/views.py::bulk_action` now scopes to a shared `_base_reminder_queryset()` — which applies the agent row filter `Q(assigned_to=user)|Q(created_by=user)` for `hierarchy_level>20` (`:681`) — instead of `Reminder.objects`, so an agent can no longer complete/cancel/reschedule/reassign teammates' reminders; and `reassign` now gates the target on an active `TenantMembership` (`:709-724`) — no more global-User cross-tenant reassign. Tests: `test_recalls.py::TestReminderBulkActionSecurity` (9).
> 2. **Messaging TICKET-branch cross-tenant participant injection → FIXED at BOTH layers** (closes pass-10 #1). `messaging/serializers.py::ConversationCreateSerializer.validate()` re-filters TICKET `user_ids` to active tenant members (`:423-437`), and **every** write path (`_create_direct` `:475-486`, `_create_group` refresh+seed `:551-593`, `_create_ticket` `:655-666`) fails closed (yields empty when `tenant is None`). Independently, `notifications/services.py::send_notification` now suppresses live in-app+email delivery to any recipient who is **not** an active member of the notification's tenant (`:113-128`) — it is the SOLE writer to the un-tenant-scoped `notifications_{user_id}` WS group, so this closes the delivery vector even for any other unguarded injection path. (`NotificationConsumer` still has no membership check — that's precisely why the service-layer guard is the fix, not the consumer.) Tests: `test_messaging_tenant_isolation.py` (+5), `test_notifications.py` (+guard tests).
> 3. **`reassign_hub_email` internal membership guard → FIXED** (closes pass-11 #5). `inbox_hub/services.py:389-398` now raises `ValueError` unless `new_user` is an active `TenantMembership` of the email's tenant, before mutating state; all three view callers (claim / `_do_assign`) already map `ValueError`→400. The engine auto-assign path (`AssignmentEngine.assign_to`) is independent and unaffected (its candidates are already members) — no risk of 500ing a legit claim.
> 4. **Bulk ticket "delete" HARD-delete → FIXED to soft-delete + audit** (closes pass-9 #3 / pass-11 #3). `tickets/services.py:1364-1388` `bulk_update_tickets` "delete" now soft-deletes (`is_deleted`/`deleted_at`/`deleted_by`) + writes an explicit `ActivityLog.DELETED` row **atomically** (matching single-ticket field mechanics), reversible via `POST /restore/`. Tests: `test_bulk_ticket_delete.py` (6).
>
> **Go-live blockers (3):**
> 5. **Fresh-tenant seeding → FIXED.** NEW `apps/tickets/defaults.py::seed_default_ticket_config(tenant)` idempotently seeds 5 statuses (exactly one `is_default` — "Open") / 4 queues / 4 categories via the **unscoped** manager with explicit `tenant=` (the CORRECT off-context pattern — contrast the RBAC bug's `.objects.get`). Wired into BOTH real tenant-creation paths — `setup_company_page` (`tenants/frontend_views.py:535`) + the `provision_tenant` command — closing the pass-7 "fresh tenant can't create its first ticket" blocker (verified end-to-end: create → 201). `setup_queues`/`setup_ticket_statuses` commands now import the shared `DEFAULT_*` lists (single source of truth). **Deliberately NOT in `Tenant.post_save`** (would `IntegrityError`-collide with the conftest `default_status` fixture under the `(tenant,slug)` unique constraint). Tests: `test_go_live_fixes.py::TestSeedDefaultTicketConfig`+`TestProvisionTenantSeeds`.
> 6. **Billing 402 self-lockout → FIXED.** `billing/middleware.py::SubscriptionMiddleware.EXEMPT_PATH_PREFIXES` broadened `/api/v1/billing/plans/`+`webhook/` → the **whole `/api/v1/billing/`** and **added `/auth/handoff/`**, so a lapsed tenant can reach checkout/subscription (and establish a cross-host subdomain session) to re-pay instead of being walled behind the 402. A normal app path still 402s. Tests: `test_go_live_fixes.py::TestSubscriptionMiddlewareExemptions`.
> 7. **Rate limiting → ADDED.** NEW `apps/accounts/ratelimit.py` (fail-OPEN cache limiter: `LOGIN_IP` 30/15m, `LOGIN_EMAIL` 8/15m, `REGISTER_IP` 12/hr) guards the plain-Django `login_page`/`register_page` that bypass DRF (checks block before `authenticate`, resets both buckets on success, records on failure/signup); `main/settings/base.py` adds baseline `UserRateThrottle` (`user` 1000/min) + `AnonRateThrottle` (`anon` 60/min) to `DEFAULT_THROTTLE_CLASSES` (coexist cleanly with `ScopedRateThrottle` — scoped only applies where `throttle_scope` set); `knowledge/views.py::KBSearchView` gains `throttle_scope="api_heavy"`; `conftest.py` adds an autouse `clear_cache` fixture (prevents throttle-counter leakage across tests). Tests: `test_rate_limiting.py` (8).
>
> **Pass-12 NEW findings (source-verified — NOT in prior docs):**
> 1. **⚠ SECURITY/PROD — IP-based rate limits are trivially bypassable / mis-keyed because no trusted-proxy config is set.** Neither `NUM_PROXIES` nor `SECURE_PROXY_SSL_HEADER` is set anywhere in `main/settings/*.py`. **(A)** `apps/accounts/ratelimit.py::client_ip` (`:19-24`) reads the leftmost client-supplied `X-Forwarded-For`, so an attacker rotating a spoofed XFF gets a fresh bucket per request and **defeats LOGIN_IP/REGISTER_IP entirely** (the exact flood the REGISTER_IP guard targets). **(B)** DRF's new `AnonRateThrottle` (no `NUM_PROXIES`) keys on `REMOTE_ADDR` — behind nginx that's the proxy IP, identical for every client, so **ALL anonymous traffic shares one 60/min bucket** → false 429s on the public plans/CSAT/login-API endpoints under modest load. **Prod fix: set `NUM_PROXIES` (or a trusted-proxy XFF parse) before relying on these limits.**
> 2. **⚠ The NEW self-service `setup_company` onboarding path is a LIVE trigger of the deferred zero-perm RBAC bug.** `setup_company_page` (`frontend_views.py:493,525`) runs on the bare domain and does `Tenant.objects.create(...)` with **no tenant bound in context**, so the `Tenant.post_save → create_default_roles → _assign_default_role_permissions` chain hits the `Role.objects.get` fail-closed bug and seeds ALL 7 roles (incl. **Admin**) with **0 explicit permissions** — the workspace runs purely on the coarse hierarchy fallback. The new ticket-config seeding was added right beside this but does NOT fix the role-permission seeding (that is the [[project-rbac-remediation-plan]] deferred work). **Every real-world self-service signup produces a zero-explicit-perm tenant.**
> 3. **⚠ Single-ticket soft-delete writes NO explicit DELETED audit row** — the working-tree fix hardened only the bulk path. `TicketViewSet.perform_destroy` (`views.py:2364-2370`) stamps `is_deleted`/`deleted_at`/`deleted_by` but does NOT call `log_activity`, and `log_ticket_activity` (`signals.py:218-277`) tracks only status/priority/assignee — NOT `is_deleted` — so a single-ticket DELETE leaves **no audit record of the deletion** (only `deleted_by` on the row), while bulk delete now DOES write a `DELETED` ActivityLog. Audit divergence, opposite direction from the bug just fixed.
> 4. **`ColumnSerializer.get_card_count` inflates counts with soft-deleted tickets' orphan cards** (`kanban/serializers.py:56-60` = `obj.cards.count()`, no orphan filter) — only the rendered board (`BoardDetailSerializer`, `:213-222`) hides them. So board LIST / column endpoints over-report card counts by the number of soft-deleted tickets' cards. Pre-existing; newly relevant now that soft-delete is the universal delete path.
> 5. **Two operator tenant-creation paths still seed NO ticket config** — superadmin `TenantViewSet.create` API (`tenants/views.py`, plain ModelSerializer) + Django-admin `TenantAdmin`. Only the two self-service paths seed. Low (operator-only), but a superadmin-provisioned tenant still can't create its first ticket until seeded. (The API `AuthViewSet.register` creates only an inactive User, never a Tenant — correctly needs no seeding.)
> 6. **A legit user with the correct password but unverified email is counted as a FAILED login** (`frontend_views.py:319,327-330`) — `authenticate()` returns None for inactive (unverified) users, and every None records a LOGIN hit, so 8 correct-password-pre-verification attempts lock them out 15 min with a "too many attempts" message instead of the "verify your email" hint. UX foot-gun, not a hole. (Minor: reminder `bulk_action`'s `count() != len(reminder_ids)` guard 404s on a duplicate id in the payload — harmless fail-closed quirk.)
>
> **Pass-12 CORRECTIONS to prior wording (do NOT regress):**
> - **pass-11 finding #2 (soft-deleted tickets leave orphan kanban cards) = CONFIRMED NON-BUG.** The `CardPosition` rows DO survive a soft-delete (intentional — so `POST /tickets/{id}/restore/` brings the card back), but `BoardDetailSerializer` HIDES any card whose content object resolves to None via the `SoftDeleteTenantManager` (`kanban/serializers.py:213-222`; hiding landed in commit `79eeb88`). And now that **both** single-ticket and bulk delete soft-delete, **NO API ticket-delete path fires `post_delete`** — `remove_kanban_cards_on_ticket_delete` (`signals.py:642`) only serves genuine hard deletes (admin/shell/CASCADE); it is a safety net, effectively never exercised by product usage. (The `get_card_count` over-count in pass-12 #4 is the one place ghost cards still surface — as a number.)
> - **The `EXEMPT` list that changed is the BILLING `SubscriptionMiddleware`'s, NOT `TenantMiddleware`'s.** `TenantMiddleware.EXEMPT_PATH_PREFIXES` = **17, unchanged** (`/auth/handoff/` is deliberately NOT in it — explicit NOTE comment). The billing middleware's list gained the whole `/api/v1/billing/` + `/auth/handoff/`. Don't conflate the two exempt lists.
> - **`agents/list.html:210` + `emails/list.html:456` avatar arrays are CSS-TOKEN arrays (`var(--crm-…)`), not raw-hex arrays** — theme-check-clean (neither file in the baseline). They still hash a multi-slot palette (split-brain identity colours vs the 8 single-red templates), but it is NOT a hex-leak, so drop any "byte-identical hex arrays" phrasing.
>
> **Prior findings NOW FIXED by the working tree (re-grade — do NOT re-report as open):** pass-10 #1 (TICKET cross-tenant injection) · pass-10 #4 (reminder reassign global-User) · pass-11 #1 (reminder `bulk_action` IDOR) · pass-11 #3 (bulk delete no-audit/hard-delete) · pass-11 #5 (`reassign_hub_email` membership). **Still DEFERRED / open:** the zero-perm role seeding ([[project-rbac-remediation-plan]] — now LIVE-triggered by self-service onboarding, pass-12 #2) · the assignee-facing SLA/escalation notifications still deep-link `/emails/` (post-triage-lens-removal residual) · raw-role drift (10 sites) · the X-Forwarded-For / `NUM_PROXIES` prod gap (pass-12 #1) · single-ticket delete audit gap (pass-12 #3).
>
> ---
>
> Last refreshed: **2026-07-09 (pass 11 — clean-room re-verification, docs detached)** — a fresh source-only pass with CLAUDE.md + MEMORY.md **detached**: I fanned **4 parallel source-verification agents** (security/cross-tenant + inbox-hub state-machine + tickets services/signals · CRM/reminders/api-keys/custom-fields/KB/newsfeed/contacts · frontend JS/CSS/templates · infra/settings/celery/billing/voip/tenancy), each told to open the cited file and adversarially **CONFIRM / REFUTE / CORRECT** every pass-10 finding against current source AND hunt for anything new — plus my own live Django-shell/DB checks, full `pytest`, and every quality gate re-run today. **Verdict: the pass-10 doc holds up ~99% under fresh scrutiny — ALL 8 pass-10 NEW findings + ALL 6 pass-10 corrections RE-CONFIRMED against current source (the two highest-severity — messaging TICKET-branch cross-tenant injection + `change_ticket_status` skipping validation — I re-verified line-by-line myself); this pass adds 5 genuinely NEW findings + 6 precision corrections (below).** The **code is byte-identical to the pass-10 snapshot** — HEAD `6c6662d` **== `origin/main`**, no `*.py` newer than the 2026-07-02 SLA/triage batch, same **28 `M` + 4 `??`** working tree (32 porcelain lines). Every registry/enum/LOC count re-measured today matches pass 10 exactly. Verified against branch **`main`** @ HEAD **`6c6662d`** (`new updates`, Tue 2026-06-23).
>
> **Pass-11 gates (re-run 2026-07-09, all GREEN):** `pytest -q` = **1078 passed / 22 skipped / 1 xfailed** (1101 collected, 213s SQLite); `makemigrations --check` clean; `manage.py check` clean; `scripts/check_theme.py` OK (**137** literals tracked vs baseline 145/10); `ruff check .` = **197** (non-blocking, pre-existing). Live-DB re-run: **71 `accounts_permission` rows** vs 69 code codenames (orphans `ticket_category.view`+`ticket_status.view`); **zero-perm role seeding reproduced** (only `straat-x` correct: admin 69/manager 59/team-lead 40/agent 24/it 25/hr 25 — all 9 other tenants 0 across every role); `HubEmail.ACTIVE_SLA_STATES` = 5 (NEW/ASSIGNED/IN_PROGRESS/PENDING_AGENT/ESCALATED); `derive_palette` 24; base.py reads **39** distinct env keys (~40). Registry re-counts all match: 91 first-party models · 122 migrations · 46 INSTALLED_APPS · 14 middleware · 12 Beat · 27 tasks · 43 receivers · 76 admin · 8 commands · 36 frontend routes · 17 EXEMPT prefixes · NotificationType 21 · INTERNAL_ONLY 6 · ActivityLog 34 · TicketActivity 27 · HubEmail 9/4 · ACTION_MAP dup `mark_all_read` @46+95.
>
> **Pass-11 NEW findings (source-verified, most independently re-confirmed by me):**
> 1. **⚠ SECURITY — `ReminderViewSet.bulk_action` bypasses the agent row-restriction (horizontal priv-esc / IDOR).** It builds its target set as `Reminder.objects.filter(id__in=reminder_ids)` (`crm/views.py:665`) — the tenant-scoped manager, which blocks cross-tenant — but does **NOT** use `self.get_queryset()`, which additionally applies the agent row filter `Q(assigned_to=user) | Q(created_by=user)` for members with `effective_role.hierarchy_level > 20` (`:390-393`). Every *single-item* action (`complete`/`cancel`/`reschedule`) honours it via `get_object()`; the bulk action does not. The `found_count != len(reminder_ids)` guard (`:667`) only checks tenant visibility, not ownership. **So a low-privilege agent can `complete`/`cancel`/`reschedule`/`reassign` ANY reminder in the whole tenant** (not just their own), and — combined with pass-10 finding #4 (global-User reassign) — reassign teammates' reminders to a non-member. This is distinct from and worse than pass-10 #4.
> 2. **Soft-deleted tickets leave orphan kanban cards.** `remove_kanban_cards_on_ticket_delete` is `@receiver(post_delete)` (`tickets/signals.py:642`) so it only fires on a real DB delete; single-ticket `perform_destroy` (`tickets/views.py:2364-2370`) **soft-deletes** (`is_deleted=True`), never firing `post_delete`, so the ticket's `CardPosition` rows survive as ghost cards on every board. The **bulk** "delete" path (pass-10 #3, hard delete) *does* remove them — so the two delete paths diverge in yet another way.
> 3. **Bulk "delete" writes no audit trail and no `deleted_by`.** `bulk_update_tickets` "delete" (`tickets/services.py:1364-1369`) calls `ticket.delete()` with no `ActivityLog`/`TicketActivity` row and no `deleted_by`, unlike single-ticket soft-delete (which stamps `deleted_by` and is reversible via the restore action). Bulk-deleted tickets are permanently gone with **zero record of who deleted them** — aggravates pass-10 #3.
> 4. **`escalate_ticket` has the same lost-update as `escalate_hub_email`.** `ticket.escalation_count = (ticket.escalation_count or 0) + 1` (`tickets/services.py:1008`) is an in-memory read-modify-write with no `F()`, so concurrent escalations under-count. Mirror of the hub defect in pass-10 correction #2.
> 5. **`reassign_hub_email` has no *internal* membership check (defense-in-depth gap).** The service (`inbox_hub/services.py:379-426`) never validates `new_user ∈ hub_email.tenant`; it relies entirely on the view's `_require_member` (`views.py:228`). Combined with the globally-unscoped `AssignSerializer.assignee_id` queryset (pass-10 #8), that one view-level check is the *only* barrier — any future/internal caller reaching the service directly would perform a cross-tenant assignment.
>
> **Pass-11 CORRECTIONS to pass-10 wording (do NOT regress):**
> - **The NotificationConsumer is NOT the delivery vector for pass-10 finding #1** (the TICKET-branch cross-tenant injection). Its group `notifications_{user.id}` is derived from the **server-side authenticated user**, not client input, so it is not itself a cross-tenant injection point (pass-9's "benign — per-user group" was right). The leak reaches the tenant-B user because they were injected as a **participant**, then legitimately connect their *own* notifications socket and receive the tenant-A preview — because **`send_notification` fans to `notifications_{recipient}` without verifying the recipient is a member of the notification's tenant.** The missing membership check on the consumer is real but beside the point; the true fix is at the participant-validation and `send_notification` layers.
> - **The custom-field-sync orphan bug is only for the FULL-clear case.** The signal guard `if custom_data and len(custom_data) > 0` (`custom_fields/signals.py:24`) skips sync only when `custom_data` is emptied to `{}`/`None`; a **partial** removal (a still-non-empty dict) DOES reach the delete-stale code at `custom_fields/services.py:117-124` and cleans up. So "the delete code is unreachable behind that guard" overstates — it's unreachable only for the full-clear case.
> - **`C_FORCE_ROOT=true` is on THREE PM2 processes (worker + beat + flower), and there is only ONE celery worker.** Pass-10's "both celery workers run `C_FORCE_ROOT=true`" is imprecise — `ecosystem.config.js` defines a single `kanzan-celery-worker`; `C_FORCE_ROOT` is at lines 58 (worker), 70 (beat), 82 (flower); the Flower `admin:changeme` default is at line 76.
> - **The legacy dead ticket auto-assign path is `auto_assign_ticket → get_available_agent` (arrow direction).** `auto_assign_ticket` (`agents/services.py:98`) calls `get_available_agent` (`:113`); pass-10 wrote the arrow backwards. `auto_assign_ticket` has zero callers (only a docstring mention `:37`) and `get_available_agent` is called *solely* by it → **both are dead.** The live path is `pick_email_agent`/`auto_assign_email_ticket` (gated by `auto_assign_inbound_email_tickets`, default False).
> - **The UTC-vs-local Beat mismatch has a concrete effect on the crontab tasks:** with `CELERY_TIMEZONE="UTC"` (vs `TIME_ZONE="Asia/Kuala_Lumpur"`, +8), `kb-stale-alert` (hour=8) and `kb-gap-digest` (Mon hour=9) fire at **16:00 / 17:00 local**, not the intended local morning. The interval beats (30s/60s/120s/etc.) are unaffected.
> - **`cleanup_stale_calls` is concretely lost via Beat today** (not just "piles up"): it is Beat-scheduled hourly onto `kanzan_voip` (`base.py:322-325` + `celery.py:27`), which no PM2 worker's `-Q` consumes — so the one voip task that fires without `run_ari_listener` never executes and its messages accumulate unbounded in Redis; orphaned `CallLog` rows stuck in an active state are never reaped.
>
> ---
>
> **Pass 10 (2026-07-08 — independent re-derivation, docs detached)** — clean-room re-derivation with CLAUDE.md + MEMORY.md **detached**: a background `Workflow` fanned **8 source-only subsystem agents** (frontend-JS/CSS/templates · tickets/SLA/kanban · inbox-hub engine · inbound+outbound-email/notifications/live/WS · CRM/contacts/KB/analytics/custom-fields/comments/messaging/newsfeed/notes · tenancy/RBAC/auth · infra/celery/voip/billing/settings — the frontend agent hit the structured-output cap so I re-derived frontend myself) **+ 1 adversarial whole-codebase hunter**, then a **42-verdict adversarial verify phase** (each finding fed to a skeptic told to REFUTE, several running live Django-shell/DB checks): **38 CONFIRMED / 4 PARTIAL / 0 REFUTED**. Plus my own Django-registry/`grep`/`wc`/`sqlite3` counting, full `pytest`, and every quality gate re-run today. **Verdict: the pass-9 doc holds up ~98–99% under fresh scrutiny — EVERY priority footgun RE-CONFIRMED (3 live-reproduced against db.sqlite3); this pass adds ~8 genuinely NEW findings and 6 precision CORRECTIONS to pass-9 wording (below).** Backend (Python/migration/test) is **byte-identical to the pass-9 + SLA-fix snapshot** — `git` confirms **no `*.py` is newer than the 2026-07-02 CLAUDE.md**, HEAD is unmoved, and the working tree carries the same 28 `M` + 4 `??`. Verified against branch **`main`** @ HEAD **`6c6662d`** (`new updates`, Tue 2026-06-23) **== `origin/main`**.
>
> **Pass-10 gates (re-run 2026-07-08, all GREEN):** `pytest -q` = **1078 passed / 22 skipped / 1 xfailed** (1101 collected, 210s SQLite); `makemigrations --check` clean; `manage.py check` clean; `scripts/check_theme.py` OK (**137** literals vs baseline 145/10); `ruff check .` = **197** (non-blocking, pre-existing). Live-DB checks re-run: **71 `accounts_permission` rows** vs 69 code codenames (orphans `ticket_category.view`+`ticket_status.view`); **zero-perm role seeding reproduced** (only `straat-x` correct: admin 69/manager 59/team-lead 40/agent 24/it 25/hr 25 — all 9 other tenants 0 across every role); `HubEmail.ACTIVE_SLA_STATES` = the 5 active states; env-keys 40/16/25; `derive_palette` 24. Registry re-counts all match pass 9: 91 models · 122 migrations · 46 INSTALLED_APPS · 14 middleware · 12 Beat · 27 tasks · 43 receivers · 76 admin · 8 commands · NotificationType 21 · INTERNAL_ONLY 6 · ActivityLog 34 · TicketActivity 27 · HubEmail 9/4.
>
> **Pass-10 NEW findings (all CONFIRMED by the adversarial verify phase):**
> 1. **⚠ SECURITY — the TICKET-type conversation-create path injects UNVALIDATED cross-tenant participants.** `ConversationCreateSerializer.validate` scopes `user_id` (DIRECT) and `user_ids` (GROUP) to active tenant members, but the **TICKET branch validates only `ticket_id`** (`messaging/serializers.py:413-417`); `_create_ticket` then `bulk_create`s `ConversationParticipant(user_id=uid)` straight from `data["user_ids"]` (`:597-607`) with no membership check. A tenant-A member can add a tenant-B user; when a message is posted, `notify_new_message → send_notification(tenant=A, recipient=B-user)` pushes a tenant-A message preview to `notifications_{B-user}`, which **`NotificationConsumer` delivers with NO tenant/membership check** (`notifications/consumers.py:39-64`). The DIRECT/GROUP paths were explicitly hardened in Sprint 0; the TICKET path was missed. `NotificationConsumer`'s missing membership check (pass-9 called it "benign — per-user group") is the delivery vector that makes this leak reach a non-member.
> 2. **`change_ticket_status` does NOT call `validate_status_transition`** (`tickets/services.py:586-622`). Only `perform_update` (`views.py:631-637`) and the `change_status` action validate. So **kanban `move_card` drags and `bulk_update_tickets` "change_status" perform UNVALIDATED status transitions** — the `ALLOWED_TRANSITIONS` guard is bypassable via those two surfaces.
> 3. **`sync_kanban_card_on_status_change` silently relocates the user's drop** when a board has >1 column mapped to the same `TicketStatus`: it re-moves the card to `Column.objects.filter(board, status=new_status).first()` (lowest-order), overriding the column the user actually dropped into (`tickets/signals.py:562-577`). The pipeline-stage variant matches by column **name (`iexact`)** — renaming a column silently breaks that sync (`:616-625`).
> 4. **`ReminderViewSet.bulk_action` "reassign" accepts any global User by UUID** (`crm/views.py:693-700`) — `User.objects.filter(pk=…)` with no tenant/membership scoping, so a manager can reassign tenant reminders to a non-member (assignee unvalidated; reminders stay tenant-scoped).
> 5. **API-key `prefix` lookup is not unique-constrained** — `prefix = cleartext[:20]` is `db_index` only (`api_keys/models.py:44-48`) and auth does `APIKey.get(prefix=…)`; a prefix collision raises an uncaught `MultipleObjectsReturned` (500) instead of `AuthenticationFailed`. Vanishingly unlikely, unguarded.
> 6. **DM/GROUP cross-tenant membership validation is fail-open when `request.tenant is None`** (`messaging/serializers.py:373,397` gate the checks behind `if tenant:`), whereas add-participant explicitly DENIES on `tenant is None` (`views.py:197-205`) — inconsistent handling of the same missing-tenant condition (mostly moot since `IsTenantMember` already fall-opens on `tenant None`).
> 7. **Deprecated `stripe.error.*` module path** (`billing/webhooks.py:301`, `views.py:112,282`) — removed in stripe ≥12; safe only because the pin is `<12`. **Flower defaults to `admin:changeme`** and both celery workers run `C_FORCE_ROOT=true` (`ecosystem.config.js:58,76`).
> 8. **`AssignSerializer.assignee_id` uses `queryset=User.objects.all()`** (global/cross-tenant, `inbox_hub/serializers.py:241-243`) — mitigated because `_do_assign` calls `_require_member` before assigning; and an idempotent re-convert of an already-CONVERTED HubEmail returns the existing ticket but the view still answers **HTTP 201** (should be 200).
>
> **Pass-10 CORRECTIONS to pass-9 wording (do NOT regress):**
> - **The zero-perm role-seeding `Role.DoesNotExist` is swallowed by a DEDICATED `except Role.DoesNotExist: continue` (`tenants/signals.py:96-97`), NOT the blanket `except (ImportError, Exception)` at `:41`/`:86`.** The blanket excepts wrap ONLY the `from apps.accounts.models import Role` import lines; the role/permission write loop is OUTSIDE them. The blanket tuple is still a real code smell (redundant — `Exception` subsumes `ImportError`), but it is not the masking mechanism. (The bug itself — all 7 roles seeded 0-perm off-context — is RE-CONFIRMED live.)
> - **The `escalate_hub_email`/`transition_hub_email` residual race is a LOST-UPDATE, not counter "doubling".** `escalation_count = (self.escalation_count or 0) + 1` on each request's in-memory instance (`services.py:349`), so two concurrent escalates COLLAPSE to a single net +1 (lost update), not +2. The real symptoms are the **state stomp** (a committed convert/dismiss overwritten back to ESCALATED/IN_PROGRESS) + **duplicate side-effects** (two `EMAIL_ESCALATED` rows + two lead notifications). Also: **`select_for_update` is a NO-OP on the dev SQLite backend**, so even the guarded convert/dismiss/reassign paths only truly serialize on prod Postgres.
> - **The legacy ticket auto-assign path (`get_available_agent` → `auto_assign_ticket`) is DEAD — `auto_assign_ticket` has NO callers.** Its "ignores presence freshness / only excludes OFFLINE" flaw is real-but-dead; the LIVE inbound-email auto-assign is the separate `pick_email_agent`/`auto_assign_email_ticket` (which shares the same raw-role + OFFLINE-only weaknesses and IS reachable, gated by `auto_assign_inbound_email_tickets` default False).
> - **The dev/prod venv path divergence (`env/` vs `.venv/`) is HARMLESS in this checkout** because `env` is a **symlink → `.venv`** (confirmed on disk), so both resolve to the same interpreter; it only breaks on a fresh clone lacking the symlink. (The dev-worker watch-glob gap — no restart on `signals.py`/`models.py`/`consumers.py`/`views.py` edits — is real.)
> - **`openpyxl 3.1.5` IS installed and pinned** (`requirements/base.txt`), so the XLSX-export → CSV-bytes fallback is **LATENT, not live** — it corrupts the `.xlsx` only if openpyxl is ever removed.
> - **The `.env` outbound-mail hazard is live-local:** the working-tree `.env` (gitignored, not committed) sets `EMAIL_BACKEND=smtp.EmailBackend` + real Gmail app-password creds, overriding base.py's `filebased` default → dev mail is sent live via `smtp.gmail.com` instead of landing in `tmp/emails/`.
>
> **Pass-9 NEW findings (all source-verified; ★ = independently reproduced against live source/DB this pass):**
> 1. ★ **A tenant created OUTSIDE a bound tenant context gets ALL 7 roles with ZERO permissions.** `_assign_default_role_permissions` (`tenants/signals.py:95`) resolves roles via the **tenant-scoped** `Role.objects.get(tenant=…, slug=…)`; with no tenant in context (the `provision_tenant` command, Django-admin on the bare domain, a shell, or any test factory) `Role.objects` fail-closes to `.none()` → `Role.DoesNotExist` → `except … continue`, so every role is created with 0 perms. **Reproduced live:** only the context-bound `straat-x` tenant has correct counts (admin=69/manager=59/team-lead=40/agent=24/it=25/hr=25); every shell/test-created tenant shows **0 across all roles** and runs purely on the `HasTenantPermission` hierarchy fallback. The CORRECT `unscoped` impl (`defaults.provision_default_roles`) already exists but is **dead**. Fix = `Role.objects.get` → `Role.unscoped.get` (or wrap in `tenant_context(instance)`).
> 2. ★ **Hub terminal-state bypass — ✅ FIXED in the working tree (§SLA-Response Fix, 2026-07-02).** Was: `convert_to_ticket` guarded only "already CONVERTED" and `dismiss_hub_email` only "already DISMISSED"; both set `state` directly with no `assert_transition`, so a **DISMISSED email could be converted** and a **CONVERTED email dismissed**. Now both services `assert_transition` against a `select_for_update` re-read (convert keeps an idempotent return for CONVERTED-with-live-ticket + a recovery path for CONVERTED-with-deleted-ticket) and all three caller views map the `ValueError` → 400.
> 3. ★ **`bulk_update_tickets` "delete" HARD-deletes.** `services.py:1366` calls `ticket.delete()`; `Ticket` has **no `delete()` override**, so the bulk endpoint permanently drops rows (fires `remove_kanban_cards_on_ticket_delete`), while the single-ticket API path (`views.py:2364`) SOFT-deletes. Inconsistent, irreversible data loss via the bulk action.
> 4. **DB has 71 Permission rows vs 69 defined** in `PERMISSION_DEFINITIONS`/`ALL_CODENAMES` — 2 orphans never referenced by any role blueprint: `ticket_category.view`, `ticket_status.view`. Verified per-role grant counts: admin 69 / manager 59 / team-lead 40 / agent 24 / it 25 / hr 25.
> 5. **`ReminderScheduler` (instant popup) fetches `?mine=true` = `assigned_to` only** (`crm/views.py:405-406`), but the server's `fire_due_reminders` recipient is `assigned_to OR created_by` (`crm/tasks.py:90`) → the client-side exact-time popup never arms for reminders you *created but didn't assign to yourself* (the 30s server backstop still pops them — split behaviour between the instant popup and the backstop).
> 6. **`CallEventConsumer` has NO membership check** (only "user authed + tenant present", `voip/consumers.py:40`) AND joins tenant-wide `voip_{tenant_id}` with no per-user scoping → any authenticated user whose Host resolves the tenant receives every call's metadata. Similarly `NotificationConsumer` never verifies tenant membership (benign — group is per-user — but inconsistent).
> 7. **`get_system_user` raises `ValueError` on a tenant with zero active members** (`inbound_email/services.py:251`) → inside `process_inbound_email`'s atomic block the whole thing rolls back and the task retries 3× then marks the mail FAILED (an emptied tenant hard-fails inbound rather than parking).
> 8. **Newsfeed draft-hide filter is gated on `action in ("list","retrieve")`** (`newsfeed/views.py:92`), so `react`/`mark_read` (which call `get_object()`) do NOT re-apply it → a non-admin who knows a draft's UUID can react-to / mark-read an unpublished post (still can't see its body).
> 9. **`fire_ticket_assigned_signal` depends on the `update_fields` hint** (`tickets/signals.py:162-170`) — a reassignment saved with `update_fields=None` (a full `.save()`) or omitting `"assignee"` emits NO `ticket_assigned` → no assignment notification.
> 10. **`fire_webhooks` dispatches via `.delay()`, NOT `on_commit`** (`tickets/services.py:142`) → if the enclosing transaction rolls back, the webhook still fires a payload for a ticket that no longer exists. Also: `kanzan_webhooks` queue is **consumed but has no producer** (`apps/billing/tasks.py` doesn't exist; ticket webhooks route to `kanzan_default` via `*`).
> 11. **Two more STALE reversed-rule comments** beyond `get_queryset:577-579`: `tickets/views.py:557-558` ("agents see all tickets" — wrong, agents ARE filtered) and `accounts/permissions.py:214-216` (same OLD "handed-off leaves their view" text next to `agent_can_see_ticket`). Minor new observations: `KBSearchGap.source` accepts/stores an invalid `customer` value (choices are `agent|portal`); `KBVote` is keyed on `session_key` not user though its vote schema claims "idempotent per user"; palette **field defaults `#6366F1`/`#F59E0B`** (indigo/amber, `tenants/models.py:207-228`) differ from the code defaults `#C1121F`/`#E11D2D`, so a freshly-seeded tenant renders indigo, not crimson, until edited.
>
> **Pass-8 additions RE-CONFIRMED (still true, source-verified pass 9):** stale `TicketViewSet.get_queryset` comment (`tickets/views.py:577-579`); outbound `send_ticket_email` sends SYNCHRONOUSLY and writes the `out:` threading row only after a successful send (`tickets/email_service.py:236-255`); `_add_reply_to_ticket` deliberately skips `ticket_comment_created` (reply-loop guard — and its would-be skip `contact.email==author.email` wouldn't even fire because `author` is the system user, `signal_handlers.py:198`) and reopens resolved/paused tickets; `CustomFieldValue` orphan rows (delete code `services.py:116-124` unreachable behind the `len>0` signal guard `signals.py:24`); `send_gap_digest` `<=10`; dashboard `.slice(0,15)` + newsfeed-modal → `{% block overlays %}` + Chart.js `height:200px` fix.
>
> **Pass-8/earlier claims CORRECTED or nuanced this pass (do NOT regress):**
> - **`resume_from_wait` tenant-scoping is SAFE in production but latent off-request.** Its `_add_reply_to_ticket` caller binds `with tenant_context(tenant)`, so the scoped `TicketStatus.objects` never falls to `.none()` there (pass-8's "SAFE" holds). BUT `resume_from_wait`/`merge_tickets`/`split_ticket` query `TicketStatus.objects` with **no explicit `tenant=`** (`services.py:573,1570,1695`) — a silent no-op/`.none()` if ever called outside a tenant-bound request (Celery/shell). Latent, not a live bug.
> - **The "`ecosystem.dev` watch omits `consumers.py` → consumer edits don't reload" claim is imprecise.** The dev celery WORKER's watch globs omit `consumers.py`/`signals.py`/`views.py`/`models.py`, but consumers run under `runserver` (kanzan-django), whose StatReloader **does** reload them. The real gap is the WORKER not restarting on `signals.py`/`models.py`/`views.py` edits (consumers aren't worker code anyway).
> - Still-true refutations from pass 8: newsfeed drafts are NOT broadcast (`newsfeed/signals.py:46-47` early-returns if unpublished); the `send_ticket_email` "async" docstring is a doc-only nuance (delivery IS synchronous — see the still-open footgun).
>
> **⚠ WORKING TREE now carries FIVE layered uncommitted UI passes PLUS the Inbox-Hub SLA-response fix (2026-07-02, post-pass-9 — the FIRST uncommitted Python/migration/test change; see §SLA-Response Fix)** on top of `6c6662d` (CSS + template markup + inline `<script>` + `static/js/{app,inbox-hub}.js` + `apps/inbox_hub/{models,services,views,tasks}.py` + `apps/inbound_email/api_views.py` + mig `inbox_hub/0002` + `tests/test_inbox_hub_sla_response.py`): passes 1–4 as before (single-red avatars + dashboard sizing — §Uncommitted UI Pass; **UI/CSS Consistency Pass 2**; offcanvas-backdrop fix + landing pricing hidden — §Overlays/Offcanvas Pass; **Responsive-Breakpoint Hardening + overlay-block migration** — §Responsive/Overlays Pass) **PLUS a 5th refinement (NEW this pass — §Responsive Refinement Pass):** (a) **`base.html` mobile-detection rewritten** — the touch-heuristic `window.screen.width` check was REPLACED by a pure `window.innerWidth < 992` (+`<576`) `applyMobileClasses()` toggled on `resize`/`orientationchange`, **dropping the touch guard entirely** (so the mobile layout now correctly engages on a narrowed desktop window / DevTools emulation, which the old `screen.width` check never did); (b) a **page-entrance-animation CSS layer** (`.crm-page-content.fade-in` + two `kz-fade-up` shell-sweep selector groups + `.td-cselect-menu` popover fade, `custom-v15.css:~22634-22803`) undocumented by the "four passes" summary; (c) **dashboard newsfeed-carousel height-sync** (`syncCarouselHeight()`) + the **Chart.js `.fd-trends-canvas-wrap { height:200px }`** ResizeObserver-growth fix. `git status --porcelain` (2026-07-02, post SLA-fix + §Mobile Triage Pass + §Triage-Lens Removal) = **28 `M` + 3 `??`** (the 20 UI-pass `M` files + `M apps/inbox_hub/{models,services,views,tasks,assignment}.py`, `M apps/inbound_email/api_views.py`, `M static/js/inbox-hub.js`, `M tests/test_inbox_hub_routing_assignment.py`; `?? docs/testing/`, `?? apps/inbox_hub/migrations/0002_active_sla_index_covers_escalated.py`, `?? apps/inbox_hub/migrations/0003_backfill_inbound_assignee_handoff.py`, `?? tests/test_inbox_hub_sla_response.py`). Gates GREEN (re-run 2026-07-02): theme-check **137** (baseline 145/10), migrate-check clean (**122 migrations**, `inbox_hub/0002`+`0003` applied to dev db.sqlite3), `manage.py check` clean, `pytest` GREEN (**1078 pass / 22 skip / 1 xfail**, 1101 collected — 25 SLA + 2 handoff tests new), `ruff` 197 (non-blocking, all pre-existing — the changed files are clean). Stale local branches: `feature/api-keys` (@`fe0ad66`), `land-inflight-work` (@`91fba10`), `theming-refactor` (@`7db5c24`), `qa/sprint-0-critical-fixes` (@`59f3417`, the merged Sprint-0 source).
>
> **Current LOC/count facts (re-measured pass 9 via `wc -l` / `git show` — these SUPERSEDE the pass-8 figures, which the working tree has since outgrown):**
> 1. **`static/css/custom-v15.css` = 25,404 LOC** working tree (25,330 after the 5th refinement + ~74 from the §Mobile Triage Pass chip-bar/one-pane CSS). **Committed HEAD = 25,246.** `custom.css` = 20,431 (NOT loaded, theme-check-allowlisted).
> 2. **`templates/base.html` = 318 lines** working tree (pass-8's `315` is now stale; committed HEAD = 292) — the mobile-detection rewrite added the extra lines.
> 3. **`static/js/app.js` = 1,288 LOC** working tree (committed HEAD = 1,273 — **unchanged since pass 8**); total `static/js/` = **5,934 LOC** across 14 modules. Only `inbox-hub.js` (`?v=13`) is cache-busted; the other 13 are unversioned.
> 4. **Single-red avatars still cover 8 templates** — working-tree `grep` confirms `return 'var(--crm-avatar-bg)'` in `{audit_log/list, contacts/list, groups/list, kanban/board, messaging/chat, profile, tickets/detail (incl `cpAvatarColor`), users/list}.html`; **`agents/list.html:210` and `emails/list.html:456` STILL hash a 10-slot `COLORS` palette** (byte-identical arrays) → split-brain identity colours. Both the `--crm-leading-*` AND `--crm-space-*` token scales have **0 uses** (dead-decorative); `--crm-primary-rgb` (193,18,31) has 213 call-sites, `--crm-accent-rgb` (225,29,45) 79.
>
> **8 findings first surfaced pass 7, ALL re-confirmed from source in pass 8 (most from the headless 2026-06-29 QA run):**
> 1. **Analytics Export button posts to a 404 route.** `settings/tenant.html:2637` (+`:4538`) POSTs `/api/v1/analytics/export-jobs/`, but the DRF router registers the viewset at **`exports`** (`analytics/urls.py:24`, basename `exportjob`) → real route is `/api/v1/analytics/exports/`. The Settings "Start Export" button cannot work as wired.
> 2. **KB `vote` schema mismatch (docs vs handler).** `ArticleViewSet.vote` reads **`request.data.get("helpful", True)`** (`knowledge/views.py:529,536`) but its own `@extend_schema` description + `OpenApiExample`s advertise **`{value: 1}` / `{value: -1}`** (`views.py:518-522`). A client following Swagger sends `{value:…}`, which is ignored and silently defaults to `helpful=True`.
> 3. **No custom 404 / 500 / 402 templates.** `templates/pages/` ships only `403.html`; `main/urls.py` defines **no `handler404`/`handler500`** → Django's bare default error pages render in prod.
> 4. **A fresh tenant seeds 0 queues / statuses / categories.** `Tenant.post_save` (`tenants/signals.py:19-73`) seeds only `TenantSettings` + 7 roles — **no `Queue`/`TicketStatus`/`TicketCategory`**, so the first "Create Ticket" is impossible until an admin runs `setup_queues` + `setup_ticket_statuses` (which exist but aren't auto-run).
> 5. **XLSX-without-openpyxl writes CSV bytes into a `.xlsx`.** `analytics/tasks.py:227-245` `_generate_xlsx` falls back to `_generate_csv` on `ImportError` but keeps the `.xlsx` filename → a corrupt-format download.
> 6. **`/contacts/new/` is a SOFT break, not a 404.** `contacts/create/` is registered before `contacts/<str:contact_id>/` (`frontend_urls.py:30-31`), so `/contacts/new/` greedily matches the **detail** route with `contact_id="new"` → renders the detail shell (HTTP 200) that then fails to load. (Command-palette "New Contact" → this dead link; real route `/contacts/create/`.)
> 7. **`.env` ships `EMAIL_BACKEND=smtp.EmailBackend` → `smtp.gmail.com` with real app-password creds** (gitignored, not leaked). Dev mail therefore does NOT land in `tmp/emails/` (newest capture is 2026-06-09). To capture locally, swap to `filebased.EmailBackend`. (The 2026-06-29 QA report's "empty creds" note is stale — creds are present.)
> 8. **`ecosystem.dev.config.js` worker watch globs omit `apps/*/consumers.py`** (only `tasks.py`/`services.py`/`main/celery.py`). ⚠ **Pass-9 nuance:** consumers DO reload under `runserver`'s StatReloader — the real gap is the celery WORKER not restarting on `signals.py`/`models.py`/`views.py` edits (consumers aren't worker code). Also: `analytics` `permission_resource="export"` has **no `ACTION_MAP` codename** → silently falls to the hierarchy default; and dev PM2 uses venv `env/` while the Makefile uses `.venv/` (path divergence).
>
> **The settings-shadow footgun stays DOWNGRADED (re-confirmed):** `apps/inbound_email/services.py` has **NO module-level `from django.conf import settings`** — Django settings are imported locally as `dj_settings` (`services.py:81,145`). So `settings = getattr(tenant,"settings",None)` (`services.py:361`) only shadows the *name* in that function block — a readability hazard, **not** an active collision bug.
>
> **State of `main` @ `6c6662d` (last commit Tue 2026-06-23, 43 files, +2786/−2425):**
> 1. **Feature C — "agent-decides ticket confirmation email" + instant ReminderScheduler.** `TenantSettings.auto_send_ticket_created_email` default **True → False** (mig `tenants/0011` = `AlterField` + `RunPython` disabling existing `True` rows); the inbound auto-send guard inverted to "send only when explicitly on" (`settings_obj is not None and …`, `inbound_email/services.py:483`); the settings toggle renders unchecked; a new client-side `ReminderScheduler` IIFE in `app.js` fires the reminder-due popup at the *exact* due time (30s server task = backstop). See **§Feature C**.
> 2. **"Emails / Inbox" UI rename + route swap.** The triage cockpit is now **"Emails"** at **`/emails/`** (view `inbox_hub_page`, url name `emails`); the personal page is now **"Inbox"** at **`/inbox/`** (view `emails_page`, url name `inbox`). View funcs / template dirs / API / the `inbox_hub` app keep their original names (intentional name↔label mismatch); legacy `/inbox-hub/` 302-redirects to `/emails/`. The cockpit also gained a real **assignee chip**; `inbox-hub.js ?v=13`. See **§Emails/Inbox Rename**.
> 3. **Two behaviour reversals (the historical docs described the OPPOSITE):**
>    - **`tickets/access.py` — a creator KEEPS a ticket after handing it off.** `agent_visible_tickets_q` = `Q(assignee=user) | Q(created_by=user)` (was `… & Q(assignee__isnull=True)`, `access.py:29`). A triager who converts an inbound email and routes it to a teammate **still sees it** in their own list/detail.
>    - **`TICKET_ASSIGNED` is in `INTERNAL_ONLY_TYPES`** (**6** members, `notifications/services.py:23-30`) — ticket-assignment notifications are **in-app/WS only, never emailed**.
> 4. **Newsfeed 24-hour auto-expiry + newsfeed/notes RBAC regression tests.** `NewsPostViewSet.perform_create` defaults `expires_at = now + 24h` (explicit expiry honoured); `get_queryset` filters out expired posts. `tests/test_newsfeed_notes_rbac.py`: newsfeed create/update/delete = **Admin/Manager only** (`IsTenantAdminOrManager`), drafts hidden from non-admins (`effective_role.hierarchy_level > 20`), draft-broadcast suppressed, **notes strictly per-user** (cross-user fetch → 404). See **§Newsfeed/Notes**.
> 5. **A broad UI-consistency tokenization + a11y pass** across ~18 templates. `base.html` toast container's hardcoded inline `z-index:1090` was **REMOVED** — now `#toastContainer { z-index: var(--crm-z-toast) }` (`custom-v15.css:14776`); the old hex-check footgun is closed.
>
> **Quality gates (re-run 2026-07-02, pass 9, against `main` + the 5 uncommitted UI passes):** full `pytest -q` = **1051 passed / 0 failed / 22 skipped / 1 xfailed** ✅ **GREEN** (205.64s on SQLite; **1074 collected**). `makemigrations --check --dry-run` = **"No changes detected" (exit 0)**. `python manage.py check` = **clean**. `python scripts/check_theme.py` **PASSES** (runtime reports **"137 pre-existing hex literals tracked"**; **baseline JSON 145 / 10 files**, intentionally un-lowered as a regression ceiling — so it is now stale-HIGH, see footguns). `ruff check .` = **197 errors** (157 auto-fixable; mostly F401/F841/F541 — non-blocking). CI runs the same gates against **PostgreSQL 16 + Redis 7** on every push-to-`main` + PR (ruff `continue-on-error`; migrate-check / theme-check / pytest blocking).
>
> **✅ Gaps the historical docs flagged that are now CLOSED on `main` (re-verified 2026-06-29):** authed `/media/` (`apps/attachments/media_views.py::serve_protected_media`, X-Accel in prod gated by `USE_X_ACCEL_REDIRECT`); `main/checks.py::kanzen.E001` hard-fails a deploy check when `DEBUG is True`; Company custom-fields synced (`custom_fields/signals.py` `Company.post_save`); internal-comment bodies redacted on the wire (`comments/signals.py:39` → `body: None if is_internal`); Hub `first_responded_at` stamped by `transition_hub_email`; billing VoIP entitlement seeded (mig `billing/0003`) + re-subscribe repoint; messaging cross-tenant user scoping; IMAP per-`(tenant, message_id)` dedup; first CI; API-register email-verify bypass; Stripe webhook replay guard (cache marker set only after handler succeeds); message-edit silent-no-op; 3 unwired webhook events; **toast inline z-index removed**; **`InboundEmail.Status.PARKED_IN_HUB` IS written** by `park_email_in_hub` (`services.py:66-67` — the old "write-dead" claim is OBSOLETE).
>
> **Still-open footguns (pre-existing, carried forward — re-confirmed present 2026-07-02, pass 9):** **raw `role` vs `effective_role` drift at 10 runtime query sites** (+ the JWT `role` claim `accounts/serializers.py:379` embeds the raw slug during a temp-role window, + 2 sites in the `seed_inbox_hub_defaults` command) — 4 user-facing routing/visibility (`agents/services.py:226` `pick_email_agent` `==30`, `inbox_hub/assignment.py:246` `_candidate_user_ids` no-dept fallback `==30` [was `:228` — shifted by the §Triage-Lens-Removal handoff insert], `tickets/views.py:1052` `teammates` `≤30` & `:1115` `team_progress` candidate filter `≤30` [the access GATE at `:1103` correctly uses `effective_role`]) + 6 recipient/actor selection (`tickets/tasks.py:165,329` `≤20`, `knowledge/tasks.py:55` `≤10`, `knowledge/views.py:223` `≤20`, `crm/tasks.py:279` `≤20` [in a DEAD task], `inbound_email/services.py:234` `get_system_user` `==10`); model `clean()` validators never called by `save()` (`Ticket` assignee `models.py:579`, `Account.health_score` `contacts/models.py:49`; `Contact.lead_score` has **no `clean()` at all** — nightly task is the only clamp); `check_overdue_reminders` + `check_sla_breach_warnings` DEAD (defined, not in Beat); `kanzan_voip` queue unconsumed (both PM2 workers' `-Q` exclude it) + `run_ari_listener` not in PM2 + `cleanup_stale_calls` itself stuck in that queue + `CallEventConsumer` tenant-wide (no per-user scoping) → **VoIP is structurally non-functional in the default PM2 runtime**; `Plan.has_call_recording` flag has **zero readers** (write-dead, seeded+backfilled, never consulted); `get_available_agent`/`pick_email_agent` gate only on `status==ONLINE`, NOT `is_assignable` (route to a stale/away agent) — ⚠ **pass-10:** the `get_available_agent → auto_assign_ticket` legacy TICKET path is **DEAD (no callers)**; only `pick_email_agent`/`auto_assign_email_ticket` is live (gated by `auto_assign_inbound_email_tickets`, default False); `AgentAvailability._within_working_hours` uses **server-local** tz (`models.py:246`), not per-tenant; `KBRevision`/`KBTicketLink` **dead-everything** (class defs only — never instantiated/queried), `require_feature` 100% dead; **`kb_search` has NO SQLite `icontains` fallback despite the `KBSearchView` `@extend_schema` description (`views.py:546`) claiming one** → it builds `.filter(search_vector=…)` unconditionally and **raises on SQLite** (`search.py`); custom-field sync **skips when `custom_data` is cleared to `{}`** → orphaned `CustomFieldValue` rows never deleted (the delete-stale-values code at `custom_fields/services.py:117-124` EXISTS but is **unreachable** behind the signal's `if custom_data and len(custom_data)>0` guard at `signals.py:24`); **`TicketViewSet.get_queryset` carries a STALE inline comment** (`tickets/views.py:577-579`) still describing the OLD reversed "handed-off ticket leaves the creator's view" rule (code is `Q(assignee)|Q(created_by)` — creator KEEPS it); **outbound `send_ticket_email` sends SYNCHRONOUSLY and records the `out:` threading `InboundEmail` row only AFTER a successful send** (`tickets/email_service.py:236-255`) → a send failure leaves no threading anchor; ~~`escalate_hub_email` re-escalation bump~~ + ~~cockpit-flow `first_responded_at` never stamped → false response-breach + auto-escalate~~ **both ✅ FIXED in the working tree (§SLA-Response Fix)** — escalate is now a no-op without a genuine transition, and claim/self-assign/convert stamp `first_responded_at` (manager routing deliberately does NOT); Hub SLA is still wall-clock (no business-hours math); cockpit still never calls `transition`/`claim`/`escalate`/`note` (dead-but-present surface — no longer SLA-load-bearing); ~~auto-assign sets only `HubEmail.assignee` (NOT `InboundEmail.assignee`)~~ **✅ FIXED (§Triage-Lens Removal, 2026-07-02)** — `assign_to` now stamps the `InboundEmail` handoff (assignee + `inbox_status=PENDING` + `is_read=False`); `ContactEvent` emits no live events; `contacts/signals.py` docstring **wrongly** claims `last_activity_at` save fires `contact.updated` (it's a `.update()`, no signal); command-palette `/contacts/new/` dead link; XLSX-without-openpyxl writes CSV into a `.xlsx` (⚠ **pass-10: LATENT** — `openpyxl 3.1.5` IS installed/pinned, so the fallback only fires if it's ever removed); `make logs-django` errors (in `.PHONY`, no rule body); `make stop`/`restart` skip `kanzan-smtp`; `pytest-timeout` missing from `dev.txt` (so `make test-fast` errors); `analytics.DashboardView` is `IsAuthenticated`-only (role gate is advisory inside `get()`, not enforced → any member 200s the endpoint); **`scripts/.theme_baseline.json` is stale-high** (tracks 145 / 10 files but the live scan is 137 — `contacts/list.html` baselined at 5 + `verify_email_sent.html` at 3 are both now tokenized to 0, so up to 8 new hex literals could slip into those two files undetected); the pass-6 findings (ACTION_MAP dup key · ~~partial-SLA-index gap~~ ✅FIXED [mig `inbox_hub/0002` adds `escalated` to `ih_email_active_sla_due`] · IMAP tenant=None dedup · `create_default_roles` blanket-except [`signals.py:41` AND `:86`] · Stripe handled-only replay marker · dead `provision_default_roles` + permission-less Viewer); **the 8 pass-7 findings** (analytics Export → `export-jobs` 404 vs `exports` · KB `vote` `{value}` docs vs `{helpful}` handler · no custom 404/500/402 templates · fresh tenant seeds 0 queues/statuses/categories · XLSX-without-openpyxl → corrupt `.xlsx` · `/contacts/new/` soft-break via greedy detail route · `.env` smtp-not-filebased so dev mail bypasses `tmp/emails/` · `ecosystem.dev` watch omits `consumers.py`); several STALE docstrings (`inbox_hub/urls.py` & `services.py` "Phase 1A", `log_ticket_activity` "5-second" dedup [actually 2s], `KBSearchView` SQLite fallback); **and the 11 pass-9 findings above** (zero-perm role seeding via `Role.objects.get` outside context · ~~Hub terminal-state bypass convert↔dismiss~~ ✅FIXED [§SLA-Response Fix] · `bulk_update_tickets` HARD-delete · DB 71 vs 69 perms [2 orphans] · `ReminderScheduler` `?mine=true` vs `assigned_to OR created_by` · `CallEventConsumer` no membership check + tenant-wide · `get_system_user` ValueError on empty tenant · newsfeed draft react/mark_read via UUID · `fire_ticket_assigned_signal` needs `update_fields` · `fire_webhooks` `.delay()` not on_commit + dormant `kanzan_webhooks` · 2 more stale reversed-rule comments). These are the QA audit's **Sprint 1–3** backlog (`docs/qa-audit-2026-06-14/06-REMEDIATION-PLAN.md`) + the headless **2026-06-29 QA run** (`docs/testing/qa-run-report-2026-06-29.md`).
>
> **Re-verified factual counts (2026-06-30 pass 7, authoritative — via Django app registry / wc / grep / line-by-line reads, NOT trusting the prior doc):**
> - **91 Django model classes** across 21 apps with `models.py` (per-app, from `apps.get_models()`: tickets 22, accounts 8, inbox_hub 8, knowledge 6, contacts 5, voip 5, billing 4, comments 4, analytics 4, kanban 3, messaging 3, inbound_email 3, newsfeed 3, tenants 2, notifications 2, agents 2, custom_fields 2, crm 2, api_keys 1, attachments 1, notes 1). 2 abstract bases (`TimestampedModel`, `TenantScopedModel`). 5 polymorphic GenericFK models (Attachment, Comment, ActivityLog, CustomFieldValue, CardPosition).
> - **122 migration files on disk** (120 committed — `tenants/0011` tracked; + `inbox_hub/0002` [§SLA-Response Fix] + `inbox_hub/0003` [§Triage-Lens Removal backfill] uncommitted). `makemigrations --check` clean. `apps.nav`/`main` have no `migrations/`. Latest per heavy app: accounts 0012, agents 0007, billing 0003, comments 0010, contacts 0005, crm 0005, inbound_email 0010, **inbox_hub 0003 [working tree]**, kanban 0004, knowledge 0005, notifications 0006, **tenants 0011**, tickets 0027.
> - **INSTALLED_APPS = 46** = 21 `apps.*` + `main` + 24 others (`daphne` + `jazzmin` at entries 1–2; 7× `django.contrib.*` incl. `postgres`; 15 third-party incl. drf, simplejwt, channels, allauth ×6, whitenoise, etc.). **`apps.nav` is NOT installed** — URL-only module mounted at `/api/v1/nav/`. Admin site `_registry` = **76 models** (registered by per-app `admin.py`; `main/admin.py` registers 0).
> - **MIDDLEWARE = 14 layers** (4 custom: SessionVersionMiddleware, TenantMiddleware, SubscriptionMiddleware, RateLimitHeadersMiddleware).
> - **29 `path()` in `main/urls.py`** (incl. authed `media/<path:path>`); **23 `/api/v1/*` includes** (22 unique URLConfs — `inbound-email/` dual-mounts as `emails/`). **36 frontend routes** in `apps/tenants/frontend_urls.py` (includes the legacy `/inbox-hub/`→`/emails/` redirect). **`TenantMiddleware.EXEMPT_PATH_PREFIXES` = 17.**
> - **6 WebSocket consumers** (`main/asgi.py` + `apps/{messaging,tickets,notifications,tenants,voip}/routing.py`). `apps/inbox_hub/routing.py` is the **RoutingEngine** (email→department), NOT a Channels route.
> - **27 Celery `@shared_task`** across 10 task modules; **12** in Beat (15 unscheduled — only `check_overdue_reminders` + `check_sla_breach_warnings` are genuinely DEAD; the rest are on-demand/queue-driven).
> - **43 signal receivers** (41 in `signals.py` across 10 apps + 2 in `notifications/signal_handlers.py`). **`apps/inbox_hub` has NO `signals.py`** and `apps.py` has no `ready()` — it fans events imperatively.
> - **NotificationType 21 / INTERNAL_ONLY_TYPES 6 (incl. `TICKET_ASSIGNED` + `REMINDER_DUE`) / ActivityLog action 34 (10 `EMAIL_*`) / TicketActivity Event 27 / HubEmail 9 states + 4 priorities (no "medium") / InboundEmail.Status 9 (incl. `parked_in_hub`) / Reminder.Priority 4 (HAS "medium") / CallLog.Status 9 / Webhook.EventType 8 / custom_fields 8 FieldType × 3 ModuleType.**
> - **76 test modules** (69 root `tests/test_*.py` [incl. `test_inbox_hub_sla_response.py`, working tree] + 7 app-level: tickets ×2, api_keys ×4, knowledge ×1). conftest 343 LOC. pytest.ini = 2 config directives (no `asyncio_mode`). pytest collects **1091 items**.
> - **`static/css/custom-v15.css` = 25,404 LOC** working tree (the only loaded project CSS; committed HEAD = **25,246**). `custom.css` = 20,431 LOC (committed snapshot, NOT loaded, theme-check-allowlisted in `check_theme.py`).
> - **14 JS files / 6,006 LOC** in `static/js/` working tree (`inbox-hub.js` **1,513** `?v=16` [§SLA-Response Fix + §Mobile Triage Pass + §Triage-Lens Removal], `app.js` **1,288** [committed 1,273 — the 4th UI pass touched it]). **Only `inbox-hub.js` is cache-busted**; the other 13 are unversioned (an `app.js`/`theme.js` change can serve stale without a hard refresh).
> - **48 `.html` templates**; **18 `pages/` subfolders**; **8** `pages/` root files; **6 `includes/`** (1 orphan: `kb_sidebar_widget.html`; `page_back_button.html` included by 18). **`base.html` = 318 lines** working tree (committed HEAD = 292 — the mobile-class re-eval was rewritten to pure `window.innerWidth` in the 5th refinement + the body-level `{% block overlays %}`).
> - **TicketViewSet = 31 `@action`s** (verified line-by-line, `views.py:510-2414`); **HubEmailViewSet = 10 `@action`s** (+ `list`/`retrieve` from `ListModelMixin`+`RetrieveModelMixin`, NOT `@action`): convert_to_ticket / dismiss / assign / reassign / claim / escalate / transition / note / context / attachment, + 4 config viewsets (Department/RoutingRule/HubEmailSLA/QueueRouting). **8 management commands.** Makefile **33 named targets** (`.PHONY` list longer). **`ALL_CODENAMES`/`PERMISSION_DEFINITIONS` = 69** (⚠ live DB has **71** Permission rows — 2 orphans `ticket_category.view`/`ticket_status.view`); **`ROLE_DEFINITIONS` = 6 entries** (Viewer seeded by `create_default_roles` but permission-less — leans on the ≤40 fallback). **`ACTION_MAP` = 76 lines / 75 unique keys** (dup `mark_all_read`). **derive_palette → exactly 24 keys** (module docstring undercounts at ~20; `accent_hover` aliased to raw `primary_hex`). **base.py reads 40 env keys; 25 read-but-undocumented** (`.env.example` = 16, incl. `KANZAN_FLOWER_AUTH` which base.py does NOT read).

## Project Overview

Multi-tenant CRM, Ticketing, Knowledge Base and VoIP SaaS. **Django 6.0.2 + DRF 3.16+ + Channels 4.2+ + Celery 5.4+** with Bootstrap 5.3.3 + vanilla JS frontend (SIP.js softphone, TipTap rich editor, DOMPurify sanitization). Row-level multi-tenancy via subdomain routing and **contextvars-based** tenant binding (async-safe). Admin is jazzmin-skinned, superuser-locked. PM2 process management. **CI exists** — `.github/workflows/ci.yml` runs ruff (non-blocking) + migrate-check + theme-check + `pytest` against **PostgreSQL 16 + Redis 7** on every push-to-`main`/PR; locally `make check` (lint → migrate-check → test) is the same gate.

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
├── apps/                          # 22 dirs (excl __pycache__); 21 in INSTALLED_APPS (nav is URL-only, no models.py)
│   ├── accounts/                  # Users (+is_service_account), 7-role RBAC + temp-role overrides + temp-perms intersection, invitations, profiles, UserGroups, middleware
│   ├── agents/                    # AgentAvailability (+last_seen presence heartbeat, is_assignable gate) + CustomAgentStatus + presence.py + reap-stale-presence task + load-fairness pick_email_agent (⚠ raw role @226)
│   ├── analytics/                 # Reports, dashboard widgets (⚠ DashboardView IsAuthenticated-only), exports (PDF/XLSX→CSV placeholder), calendar events
│   ├── api_keys/                  # APIKey (SHA-512) + auth class + viewset + per-key throttle + rate-limit-headers middleware + drf-spectacular extension
│   ├── attachments/               # File uploads (polymorphic GenericFK, python-magic MIME, 25MB cap); access.py (object-level authz) + media_views.py (authed /media/ serving)
│   ├── billing/                   # Stripe billing, plans, subscriptions, webhooks (5 events + replay guard), decorators (⚠ require_feature dead; NO tasks.py)
│   ├── comments/                  # Comment + Mention + CommentRead + ActivityLog (34 actions incl. 8 EMAIL_*) + LIVE signals (✅ internal-comment bodies REDACTED on wire)
│   ├── contacts/                  # Contacts, Companies, Accounts, Groups, ContactEvent (360°, NOT live-broadcast) + LIVE signals + context.py (build_contact_context, shared with Hub cockpit)
│   ├── crm/                       # Activity + Reminder (M2M contacts/tickets; +due_notified_at) + lead/account scoring + LIVE signals + fire_due_reminders task (Feature A)
│   ├── custom_fields/             # EAV custom fields per tenant + sync signals (✅ Ticket + Contact + Company)
│   ├── inbound_email/             # SMTP+IMAP ingestion; forks on TenantSettings.inbox_hub_enabled → legacy ticket-create OR park in Hub; agent email-inbox handoff; create_ticket+overrides (Feature B); ticket_overrides.py (shared validator) + attachments.py (serialize/stream customer files)
│   ├── inbox_hub/                 # Email-triage workspace: 8 models + services + RoutingEngine + AssignmentEngine + state machine + SLA task + HubEmailViewSet 10 @actions (+context +attachment) + 4 config viewsets + access.py (DEPARTMENT-scoped). NO signals.py.
│   ├── kanban/                    # Visual boards, columns (is_personal), polymorphic CardPosition; cross-status drags route through tickets service (full audit/feed/SLA)
│   ├── knowledge/                 # KB articles (PG FTS), categories, search, stale alerts, gap digest, allowed_groups M2M (⚠ KBRevision/KBTicketLink dead-everything; kb_search raises on SQLite)
│   ├── messaging/                 # Real-time conversations (WS); Conversation.source_group; attachments on messages (POST broadcast action); ✅ cross-tenant user scoping
│   ├── nav/                       # URL-only module (BadgeCountView — 7 categories; effective_role; NOT an installed app)
│   ├── newsfeed/                  # Internal announcements, reactions, read receipts + LIVE signals (draft-broadcast suppressed)
│   ├── notes/                     # Personal sticky notes (6 colors, pinning) — no signals
│   ├── notifications/             # In-app + email + WebSocket notifications (21 NotificationType incl. 5 HUB_EMAIL_* + REMINDER_DUE); NOT polymorphic (data JSONField)
│   ├── tenants/                   # Tenant model, middleware, frontend views, frontend_urls (36 routes), live broadcast layer, palette; LiveEventConsumer stamps presence on heartbeat
│   ├── tickets/                   # Core ticketing; Queue gains optional department FK; access.py (shared agent-visibility helper — creator KEEPS ticket after handoff); SLA + business hours, CSAT, pipelines, macros, webhooks, deals
│   └── voip/                      # Asterisk ARI integration, SIP softphone, call logs, recordings, queues (runtime is manual-launch — see Pitfalls)
├── main/                          # Django project root
│   ├── settings/{__init__,base,dev,prod}.py  # __init__ branches on DJANGO_DEBUG (**default False** → dev.py else prod.py); base.py holds CELERY_BEAT_SCHEDULE (12) + AGENT_PRESENCE_* + HUB_SLA_*
│   ├── checks.py                  # system checks; kanzen.E001 hard-fails if DEBUG is True (deploy tag)
│   ├── admin.py                   # SuperuserOnlyAdminSite (reassigns admin.site.__class__) + TenantFilteredAdmin mixin; registers 0 models
│   ├── celery.py                  # Celery app + queue routing (8 globs incl. default) — NO beat schedule (lives in base.py)
│   ├── asgi.py                    # ProtocolTypeRouter: HTTP + WebSocket (6 consumer endpoints, WebSocketTenantMiddleware)
│   ├── context.py                 # contextvars-based tenant context (async-safe)
│   ├── models.py / managers.py    # TimestampedModel, TenantScopedModel; TenantQuerySet, TenantAwareManager (fail-closed .none()), SoftDeleteTenantManager
│   └── urls.py                    # 29 path() (23 /api/v1/ includes; 22 unique URLConfs) + /admin/ + /api/{schema,docs}/ + /accounts/ + authed media/ + frontend ""
├── templates/                     # 48 .html files (18 subfolders under pages/)
│   ├── base.html                  # 302 lines — palette <style>, body-level {% block overlays %} (offcanvas-backdrop stacking fix, uncommitted), toast container (token-driven, inline z-index REMOVED), live-bus + live-connection JS, Flatpickr loader, sidebar-collapse FOUC fix, #reminderDueModal
│   ├── includes/                  # 6 files — navbar, sidebar (Inbox section gated {% if can_access_inbox_hub %}), softphone, messages, page_back_button (18 includes), kb_sidebar_widget (ORPHAN)
│   ├── pages/                     # 18 subfolders + 8 root html files (403, api_quickstart, calendar, dashboard, landing, login, profile, register)
│   ├── landing/landing_crm.html   # Standalone marketing page (doesn't extend base.html); ⚠ Pricing section + nav/footer pricing links commented out (uncommitted "temporarily hidden")
│   └── {auth,knowledge,notifications,tickets}/email/  # transactional email templates
├── static/
│   ├── css/custom-v15.css         # 25,330 LOC working (loaded; "Design System v9.0 Crimson Black")
│   ├── css/custom.css             # 20,431 LOC (committed snapshot, NOT loaded, allowlisted in theme check)
│   ├── images/                    # Logo, favicon (DP.png), hero artwork
│   └── js/                        # 14 vanilla-JS modules (5,934 LOC working, incl. live-bus + live-connection + inbox-hub.js 1,441 + app.js 1,288)
├── tests/                         # 69 root pytest modules + 7 app-level (76 total); 1099 collected (incl. test_inbox_hub_sla_response.py, working tree)
├── conftest.py / pytest.ini       # 343 LOC: 16 factories + 20 fixtures (3 autouse); pytest.ini = 2 config lines, no asyncio_mode
├── requirements/{base,dev,prod}.txt   # prod = -r base.txt (no extras); dev = base + tools (incl. pytest-asyncio; ⚠ still no pytest-timeout)
├── .github/workflows/ci.yml       # CI: ruff(non-blk)+migrate-check+theme-check+pytest on PG16+Redis7
├── requirements.txt               # ROOT — byte-identical duplicate of requirements/base.txt
├── ecosystem.config.js            # PM2 prod: 5 processes (django, celery-worker, celery-beat, flower, smtp)
├── ecosystem.dev.config.js        # PM2 dev: 4 processes (no SMTP, watch-mode reloads)
├── Makefile                       # ~33 targets (logs-django in .PHONY but no rule body → calling it errors)
├── docs/                          # README + architecture.md (STALE) + reference/{4 docs, STALE} + ui-consistency-audit.md (2026-06-23) + deploy/protected-media.md + qa-audit-2026-06-14/ (11 files) + testing/{manual-testing-checklist, qa-run-report-2026-06-29} (relocated 2026-06-30)
├── tmp/emails/                    # Dev email capture (filebased EmailBackend, gitignored)
├── logs/                          # PM2 log files (gitignored; no rotation)
├── media/                         # User-uploaded: tenants/{id}/… and inbound_emails/{id}/…
├── scripts/                       # check_theme.py + .theme_baseline.json (145 hex literals across 10 files)
├── db.sqlite3                     # Dev database (~12MB, gitignored)
├── celerybeat-schedule            # Celery Beat shelve file (built-in scheduler — django-celery-beat removed for Django 6 compat)
└── .env                           # base.py reads 40 keys (.env.example covers 16; 25 read-but-undocumented; KANZAN_FLOWER_AUTH documented but NOT read by base.py)
```

## Branch & Working-Tree State (2026-06-29 pass 6)

- **`main` @ `6c6662d` == `origin/main`; working tree carries FOUR layered uncommitted UI passes** (`git status --porcelain` = **19 `M` + 2 `??`**: `M CLAUDE.md`, `M static/css/custom-v15.css`, `M static/js/app.js`, `M templates/base.html`, `M templates/landing/landing_crm.html`, + 14 `M templates/pages/*.html`: audit_log/list, auth/verify_email_sent, contacts/list, dashboard, groups/list, inbound_email/list, inbox_hub/list, kanban/board, messaging/chat, profile, reminders/list, settings/tenant, tickets/detail, users/list; `?? docs/manual-testing-checklist.md`, `?? docs/qa-run-report-2026-06-29.md` → relocated to `docs/testing/` this pass). `6c6662d` ("new updates", Tue 2026-06-23) is the last commit; the changes since are this doc + §Uncommitted UI Pass + §UI/CSS Consistency Pass 2 + §Overlays/Offcanvas Pass + §Responsive/Overlays Pass below — **no Python, migration, or test changes**, but the Responsive pass **DOES modify `static/js/app.js`** (the first working-tree pass to do so). Prior history: `94468bc` (flatpickr CSS) ← `5fda34a` (PR #1 merge of `qa/sprint-0-critical-fixes`) ← `59f3417` ← `9575577`. The side branch `qa/sprint-0-critical-fixes` still exists at `59f3417`; other local branches (`feature/api-keys`, `land-inflight-work`, `theming-refactor`) are stale.
- **What `main` contains (all committed):** Sprint-0 QA/security hardening, the first CI workflow, the launch-fundamentals security pass (authed `/media/`, `main/checks.py` DEBUG enforcement, HSTS-preload), the QA-audit follow-up defect fixes, the Inbox-Hub department-access refactor, **Feature A** (reminder-due popup), **Feature B** (create-ticket overrides + the Hub convert panel), inbound-email attachments, **Feature C** (agent-decides confirmation email + ReminderScheduler), the **Emails/Inbox rename** (+ cockpit assignee chip), the **two behaviour reversals** (creator keeps tickets after handoff; `TICKET_ASSIGNED` no longer emailed), the **newsfeed 24h auto-expiry + newsfeed/notes RBAC tests**, and a broad **UI-consistency tokenization + a11y pass**.

## Uncommitted UI Pass (working tree only — still uncommitted on `6c6662d`)

A small CSS/template-only polish pass on top of `6c6662d`. No JS-logic, Python, migration, or test changes. Three files:

1. **Single-red identity avatars.** `static/css/custom-v15.css` adds two theme-reactive tokens — `--crm-avatar-bg` / `--crm-avatar-fg` — in both `:root` (light) and `[data-bs-theme="dark"]`. **Light:** bg = `var(--crm-primary)` (brand red); **Dark:** bg = `var(--crm-primary-hover)` (the brighter red, so the circle stays vivid on `#0B0B0B`/`#121212`); fg = `var(--crm-text-on-primary)` in both. `templates/pages/contacts/list.html` replaces the old 8-slot grayscale-plus-one-red `AVATAR_COLORS` palette + hashing `avatarColor(name)` with a one-liner `avatarColor(name){ return 'var(--crm-avatar-bg)'; }` (name now ignored; same call signature). Avatar text colour switched from `--crm-text-on-primary` to the new `--crm-avatar-fg`. **Net effect:** every identity avatar is now one uniform brand red, and `contacts/list.html` drops to **0 hex literals** (was 5 — the theme-check delta above).
2. **Dashboard recent-activity sizing.** `static/css/custom-v15.css` introduces `--activity-row-h: 4rem` on `.activity-list`, sets `.activity-list { max-height: calc(var(--activity-row-h) * 5) }` (so the resting viewport always ends on a whole row — ~5 rows, then scrolls) and `.activity-item { height: var(--activity-row-h) }` (fixed, uniform row height; padding tightened to `0.55rem 1.25rem`).
3. **Dashboard recent-activity DOM cap.** `templates/pages/dashboard.html` caps the rendered set to `(tickets || []).slice(0, 15)` (the tickets list endpoint ignores `?page_size`, so it returns up to PAGE_SIZE=50 rows; this keeps the DOM light since only ~5 rows are visible and the rest scroll).

## UI/CSS Consistency Pass 2 (2026-06-26 — working tree, uncommitted)

A second uncommitted CSS/template pass on top of `6c6662d` (layered over the avatar/dashboard pass above), driven by a **7-auditor design-system audit** (4 CSS-dimension + 3 surface auditors) + line-level verification of every high-severity claim. **Token-unification + correctness fixes only — no Python, migration, test, or `static/js/*` changes** (the only JS touched is inline `<script>` tweaks in templates: `role="switch"` setAttribute + the avatar one-liners below). Files: `static/css/custom-v15.css` + **10 templates** — the 5 correctness-fix templates (`settings/tenant`, `auth/verify_email_sent`, `tickets/detail`, `inbound_email/list`, `audit_log/list`) + the 6 avatar-unification templates (`users/list`, `groups/list`, `profile`, `kanban/board`, `messaging/chat` — plus `tickets/detail` + `audit_log/list` which overlap the first set). **Gates re-run GREEN:** `check_theme.py` **137** (was 140 — three `#1a1a1a` literals tokenized away; baseline still 145/10), `makemigrations --check` clean, `manage.py check` clean, full `pytest` **1051 passed / 22 skipped / 1 xfailed**. CSS `{`/`}` balanced; `custom-v15.css` was **25,292 LOC** at the end of *this* pass alone (net −32 from dead-class removal minus token additions); the live working-tree value after the §Overlays/Offcanvas Pass below is **25,298 LOC**.

### Tier A — mechanical token unification (render-identical / sub-pixel)
1. **Radius pill unification** — `border-radius: 999px` (×16) + `100px` (×9) → `var(--crm-radius-pill)`. Every site is a small badge/pill/chip (1–5px padding) where 100/999/9999px all clamp to a full pill → identical. `var(--crm-radius-pill)` count 45→70.
2. **Exact rem→token radii** — `0.5rem`→`var(--crm-radius-sm)` (×29), `0.375rem`→`-md` (×9), `0.25rem`→`-xs` (×5); standalone single-value only — directional shorthands (`0 8px 8px 0`) protected via perl lookahead `(?=\s*[;!}])`. Identical px.
3. **Exact font-size→token** — only one raw `font-size: 0.8125rem` remained → `var(--crm-text-base)` (the exact-scale sizes were already tokenized; the ~290-value off-scale cloud is LEFT RAW — Tier C).
4. **`--crm-primary-rgb: 193, 18, 31;` added to `:root`** — referenced ~195× but only defined at runtime in `base.html:52` (per-tenant palette). The CSS now carries a self-contained fallback mirroring `--crm-accent-rgb` (`:root` line ~30), so the `rgba(var(--crm-primary-rgb),a)` focus-rings/tints survive if the inline palette `<style>` ever fails to load.
5. **`--crm-leading-{tight 1.2, snug 1.35, normal 1.5, relaxed 1.65}` added** — line-height had **0 tokens** (147 raw). Forward standard; existing raw values NOT mass-migrated (visible, staged → Tier C).
6. **Off-palette slate shadows → neutral** — the 4 `.kanban-card` shadows' `rgba(15, 23, 42, …)` (Tailwind slate-900) → `rgba(11, 11, 11, …)` (palette neutral base). Hue-only.
7. **Dead-class deletion** — `.badge-dot` + 7 `.badge-dot-*`, `.card-header-flush`, `.card-header .card-header-actions`, `.card-header-title`(+`i`) — all verified 0 refs in `templates/`+`static/js/`.

### Tier B — correctness/bug fixes (visible — each fixes a defect)
1. **Off-brand blue select caret → crimson** — `.form-select:hover`/`:focus` (+1 other caret data-URI) `%232563EB` → `%23C1121F`; rest-state caret stays neutral `%236B7280`. Every dropdown was flashing a blue chevron on hover/focus.
2. **`verify_email_sent` dark-mode legibility** — the info box uses `background-color: var(--crm-surface)` (dark in dark mode) but pinned icon/text to `#1a1a1a` → illegible in dark theme; 3× `#1a1a1a` → `var(--crm-text)` (light mode ≈ identical near-black; dark flips light → legible). Removes 3 baseline hex.
3. **`agents/list` responsive header** — added `@media (max-width: 767.98px)` for `.ae-page-header { flex-direction: column }` + `.ae-page-header__right { width:100% }`, matching the responsive stacking the shared `.page-header` (users/groups) already had; the bespoke ae- header could squeeze/overflow on mobile.
4. **`role="switch"` a11y completion** — added to the 4 static `form-switch` toggles lacking it (`dndEnabled`, `sidebarCollapsedToggle`, `queueAutoAssign`, `customStatusActive`) + the 2 JS-built notification toggles (`inAppInput`/`emailInput` `setAttribute`). The 2 plain `form-check` checkboxes (`crShared`, `apiKeyRevealAck`) correctly left as checkboxes (9 `role="switch"` total now).
5. **stat-card accent token** — `inbound_email/list.html` + `audit_log/list.html` stat-card-accent `var(--bs-primary)` → `var(--crm-primary)` (theme-correct brand red, matching sibling cards).
6. **Removed invalid `gap:-.25rem`** on `#presenceAvatars` (`tickets/detail.html`) — negative `gap` is illegal CSS (silently dropped); the JS already spaces bubbles via positive `margin-right:2px`, so this was dead/contradictory. Removal = zero visual change + valid CSS.
7. **App-wide single-red identity avatars** — the committed `contacts/list.html` pass made avatars a single brand red (`var(--crm-avatar-bg)`), but **8 other surfaces still hashed a 10-slot multi-colour `AVATAR_COLORS` palette** by name (so the same person rendered a different colour per page). Unified all of them to `function <colorFn>(name){ return 'var(--crm-avatar-bg)'; }` + removed the dead arrays: `users/list`, `groups/list`, `profile`, `kanban/board`, `messaging/chat`, `audit_log/list` (`hashColor`), `tickets/detail` (`avatarColor` + the convert-panel `cpAvatarColor`). Text stays white (`--crm-text-on-primary` ≡ `--crm-avatar-fg`). Now **9 templates** resolve identities to one theme-reactive red. (Originally scoped as users/groups-only; expanded once verification found the same pattern in 6 more files — half-doing it would have created split-brain drift.)

### Two audit claims VERIFIED-FALSE and deliberately NOT applied
- **`--crm-primary-rgb` "undefined → 195 dropped declarations"** — FALSE: it IS defined at runtime in `base.html:52`; only a self-containment fallback was warranted (Tier A.4), not an emergency.
- **"autofill block breaks dark mode"** — FALSE: the `.auth-input:-webkit-autofill` `#FFFFFF`/`#0B0B0B` hex is **intentional + documented** (browser autofill ignores `var()`; comment at `custom-v15.css:~6410`) AND the auth panel is forced **always-light** regardless of theme (`[data-bs-theme="dark"] .auth-form-panel{background:#FFFFFF}`, ~6457). No dark flash; tokenizing would reintroduce the documented regression. Left as-is.

### Deferred to Tier C (visible / structural — needs design sign-off + visual QA; NOT applied)
The ~290 off-scale font-size cloud + the **15px/17px/22px type-scale gaps** + the **12px radius gap** (snapping is visible); **spacing-token adoption** (`--crm-space-*` still **0 uses** across 1,905 raw padding/margin/gap); consolidating duplicate **`.card`** (×3 — incl. an unscoped `.card:hover` `translateY(-2px)` at ~22669 overriding the `.card-hover` opt-in `−1px` at ~2818, so EVERY card lifts) / **`.btn`** (×2; `:active` scale 0.98 vs 0.97) / **`.form-control`** / **`.chip--*`** / duplicate dark-overrides + the resulting redundant **`!important`** (389); unifying the **3 email surfaces** (`ae-`/`ih-`/`tl-`) + 5 empty-state families + 2 stat-card systems + 7 badge namespaces; line-height mass-consolidation; off-palette newsfeed-category + `calendar.html` color maps. *(Avatar single-red unification — formerly Tier C — was completed app-wide; see Tier B.7.)*

## Overlays / Offcanvas Pass (2026-06-29 — working tree, uncommitted — NEW this pass)

The third uncommitted UI pass, layered on top of Pass 2. **Template-markup + comment only — no Python/migration/test/`static/js/*` changes** (the CSS file moved only via the cumulative count, 25,292 → 25,298). Three files, two changes:

1. **Body-level `{% block overlays %}` (offcanvas-backdrop stacking-context fix).** `templates/base.html` adds `{% block overlays %}{% endblock %}` as a **direct child of `<body>`** (after the page-content block, before `#toastContainer`) with an explanatory comment. **Why:** Bootstrap appends an offcanvas/modal **backdrop to the panel's PARENT node**; a panel nested inside the animated `.crm-page-content` subtree gets **trapped in that subtree's stacking context** and renders **under the sticky top bar with a content-area-only backdrop**. Hoisting the panel to a `<body>`-level block attaches the backdrop to `<body>` so the overlay covers the full viewport. (base.html grew 292 → **302 lines**.) **Any future page that puts an offcanvas/modal inside `.crm-page-content` will hit the same trapped-backdrop bug — render it through `{% block overlays %}` instead.**
2. **`#ihConvertPanel` hoisted into `{% block overlays %}`.** `templates/pages/inbox_hub/list.html` closes its content block early and re-opens `{% block overlays %}`, moving the Convert-to-Ticket offcanvas (the full ticket-override slide-over) out of the content subtree — the concrete consumer of (1).
3. **Landing-page Pricing hidden (`templates/landing/landing_crm.html`).** The nav "Pricing" link, the entire `<section>` Pricing block, and the footer "Pricing" link are wrapped in `{% comment %}…{% endcomment %}` ("temporarily hidden for now — unwrap to restore"). The 3 plans table in `billing` + `seed_plans.py` is unaffected — this is marketing-page presentation only.

> ⚠ The "base.html 292 → 302" figure above is the state *after Pass 3*; the **Responsive/Overlays Pass (Pass 4) below takes it to 315** and is the first working-tree pass to touch `static/js/app.js`.

## Responsive/Overlays Pass (2026-06-30 — working tree, uncommitted — NEW this pass)

The fourth uncommitted UI pass, layered on top of Pass 3. **CSS + template markup + inline `<script>` + `static/js/app.js`** (still NO Python/migration/test changes). This is the **first working-tree pass to modify `static/js/*`**, so every prior "no `static/js/*` changes" statement is now stale. It is a targeted **three-part responsive-correctness fix** for stale mobile state when the viewport crosses a breakpoint (e.g. exiting Chrome DevTools' device toolbar), plus the overlay-block migration extended to two more surfaces. Files (cumulative working-tree counts: `custom-v15.css` → **25,324**, `base.html` → **315**, `app.js` → **1,288**):

1. **Mobile-class re-evaluation on resize (`templates/base.html`).** The `<head>` mobile-detection IIFE was refactored from a one-shot `DOMContentLoaded` add into an `applyMobileClasses()` that uses `classList.toggle('is-mobile'/'is-mobile-html'/'is-mobile-sm', cond)` and re-runs on **`resize`** + **`orientationchange`** (a real desktop never has touch, so it never flips ON; this only *removes* a stuck mobile class). Without it, leaving DevTools emulation left `is-mobile` stuck and hid the sidebar on the desktop layout.
2. **Off-canvas / detail-pane / chat-pane close-on-widen.** Three pages got matching `resize`/`orientationchange` handlers that drop a "mobile-open" overlay class once the viewport is wide again: `static/js/app.js` (`closeSidebarIfDesktop`, `≥992px` → close the mobile off-canvas sidebar + clear backdrop + body scroll-lock); `templates/pages/reminders/list.html` (`syncDetailPaneViewport`, `>991px` → drop `#detailPane.is-mobile-open`); `templates/pages/messaging/chat.html` (`syncChatPanes`, `≥768px` → clear `.chat-sidebar-hidden` + `.chat-main-active`).
3. **Overlay-block migration extended.** `templates/pages/dashboard.html` moves both newsfeed modals (`#nfModal`, `#nfDetailModal`) out of the content block into a body-level `{% block overlays %}` (same Bootstrap-backdrop-trapping fix as `#ihConvertPanel`), and applies the recent-activity `.slice(0, 15)` DOM cap (the tickets-list endpoint ignores `?page_size` and returns up to 50). `templates/pages/inbox_hub/list.html` likewise hoists `#ihConvertPanel` into `{% block overlays %}`. `templates/pages/messaging/chat.html`, `kanban/board.html`, `profile.html`, `tickets/detail.html` (incl. `cpAvatarColor`) also carry the **single-red avatar** unification (`avatarColor(name){ return 'var(--crm-avatar-bg)'; }`) — **8 templates total now single-red; `agents/list.html` + `emails/list.html` still hash a multi-colour palette.** `tickets/detail.html` also drops the invalid `gap:-.25rem` on `#presenceAvatars`.

**Takeaway for future work:** any page placing an offcanvas/modal inside `.crm-page-content` must render it through `{% block overlays %}`; any page that toggles a "mobile-open" overlay class at selection-time must also clear it on `resize`/`orientationchange`.

## Responsive Refinement Pass (2026-07-02 — working tree, uncommitted — NEW this pass, 5th)

The fifth uncommitted UI pass, layered on top of Pass 4. **CSS + `base.html` `<script>` + `dashboard.html` markup** (still zero Python/migration/test changes; `app.js` unchanged since Pass 4). It supersedes the Pass-4 cumulative counts: `custom-v15.css` → **25,330**, `base.html` → **318** (`app.js` stays **1,288**). Three changes:

1. **`base.html` mobile-detection rewritten (the load-bearing change).** The Pass-4 detection IIFE — a **touch-gated** `('ontouchstart' in window || navigator.maxTouchPoints > 0) && window.screen.width < 992` one-shot — was REPLACED by an `applyMobileClasses()` that reads **`window.innerWidth`** (the rendered CSS viewport) and `classList.toggle('is-mobile'/'is-mobile-html'/'is-mobile-sm', cond)` at **<992px / <576px**, re-run on `DOMContentLoaded` + `resize` + `orientationchange`. **The touch heuristic is GONE entirely.** *Why:* `window.screen.width` stays at the host monitor's width under DevTools device emulation and on any narrowed desktop window, so the old `< 992` check never fired and the desktop 260px-sidebar layout leaked into the mobile viewport; the inline comment documents this. **Behavioural delta vs the doc's Pass-4 description:** the old code "only removed a stuck mobile class, never flipped ON on a real desktop"; the NEW code **intentionally engages the mobile layout on any narrow viewport** (narrowed desktop window / DevTools), which is the correct responsive behaviour. `base.html` 315 → **318**.
2. **Page-entrance-animation CSS layer (undocumented by the four-passes summary).** `custom-v15.css:~22634-22803` adds `.crm-page-content.fade-in { animation: kz-fade-in }` (the class is already on every authed page at `base.html:129`) + two `kz-fade-up` shell-sweep selector groups (round 1: `.error-page, .api-quickstart, .ae-tabs-wrap, .kb-article-container, .chat-wrapper, .pf2-shell, .rm-hero, .rm-list, .ih-shell, #createTicketForm`; round 2: `.tl-toolbar, .tl-stat-tabs, .tl-table-header, .td-sb-section, #kbAdminContent, #emailList, #inboundEmailsList, #pane-activity`) + `.td-cselect-menu.td-cselect-portal { animation: popoverFadeIn }`. A genuine global page-entrance-motion feature.
3. **Dashboard carousel + Chart.js fixes.** `dashboard.html` adds `syncCarouselHeight()` (measures the active newsfeed slide, pins `.nf-carousel__track` height, re-runs on `goToSlide`/summary-render/`resize`/`orientationchange`); `custom-v15.css` changes `.fd-trends-canvas-wrap` from `flex:1; min-height:180px` → **`height: 200px`** (Chart.js `maintainAspectRatio:false` + no definite height caused an unbounded ResizeObserver growth loop on every resize).

**Takeaway:** viewport-width breakpoints belong on `window.innerWidth`, never `window.screen.width`; a `maintainAspectRatio:false` Chart.js canvas needs a definite-height wrapper or it grows unbounded under a ResizeObserver.

## SLA-Response Fix (2026-07-02 — working tree, uncommitted — the FIRST backend change since `6c6662d`; hardened by a 2-round adversarial verify)

Fixes the review-blocker that **every actively-triaged Hub email false-breached its response SLA and auto-escalated** (the cockpit never calls `transition`, previously the only `first_responded_at` writer), plus the two adjacent state-machine defects and the pass-6 index gap. Round 1 was adversarially verified by 3 refuter lenses which surfaced 4 issues (incl. a live-reproduced 500); round 2 fixed all of them. **Files:** `apps/inbox_hub/{services,views,models,tasks}.py`, `apps/inbound_email/api_views.py`, **mig `inbox_hub/0002_active_sla_index_covers_escalated`** (applied to dev db.sqlite3), `static/js/inbox-hub.js` (**1,450 LOC**, bumped **`?v=14`** in `inbox_hub/list.html`), **`tests/test_inbox_hub_sla_response.py` (NEW, 25 tests / 5 classes)**. Full suite **1076 pass / 22 skip / 1 xfail (1099 collected)**; migrate-check + `manage.py check` clean; changed files ruff-clean.

1. **`first_responded_at` = the RESPONDER's first action, not the router's.** `reassign_hub_email` stamps it (when unset) **only when `actor is not None AND actor.pk == new_user.pk`** — i.e. claim / self-assign ("I've picked this up"); `convert_to_ticket` stamps too (converting IS the triage response). **Deliberately NOT stamped by:** a manager **routing** mail to someone else (stamping there would disarm the sat-on-mail breach + lead escalation for manually-routed mail — verifier finding), `AssignmentEngine.assign_to` (engine auto-assign), and `dismiss_hub_email` (a discard is not a response). Routed/auto-assigned-but-untouched mail **still breaches + auto-escalates** (durability layer). `transition_hub_email`'s RESPONSE_STATES stamping unchanged.
2. **`escalate_hub_email` gated on a genuine transition.** Full no-op (no count bump, no `escalated_to`, no lead re-notify, no EMAIL_ESCALATED row) unless `can_transition(old_state, ESCALATED)` — kills manual re-escalation counter inflation AND the sweep's duplicate escalation (the sweep's breach flag + `_notify_sla` still fire). **The `escalate` API action pre-checks `can_transition` → 400** so the service's silent no-op isn't a lying 200 (parity with convert/dismiss/transition; benign TOCTOU: a race falls back to the silent-no-op 200).
3. **Terminal-state guard, concurrency-safe.** `convert_to_ticket` + `dismiss_hub_email` now do their idempotent check + `assert_transition` **inside `transaction.atomic()` against a `select_for_update()` re-read** of state/converted_ticket_id/first_responded_at (asserting on the caller's unlocked instance would let two racing terminal actions both pass — verifier finding; `reassign_hub_email` already locked this way). Convert keeps the idempotent CONVERTED-with-live-ticket return + the CONVERTED-with-deleted-ticket (SET_NULL) recovery path. **All THREE callers** map the `ValueError` → 400: `HubEmailViewSet.convert_to_ticket`, `.dismiss`, **and `InboundEmailViewSet.create_ticket`** (`apps/inbound_email/api_views.py` — the personal-Inbox path; round 1 missed it → live-reproduced 500 on a dismissed hub email).
4. **Read side matches the sweep.** New **`HubEmail.ACTIVE_SLA_STATES`** classattr (single source for the 5 active states) consumed by BOTH `check_hub_sla_breaches` and the **`?sla_risk=true` lens**, which now also filters `first_responded_at__isnull=True` — responded/terminal rows no longer sit in "SLA at risk" forever (mirrors `tickets/filters.py`). `inbox-hub.js::renderSlaBadge` short-circuits to a neutral **"responded"** badge when `row.first_responded_at` is set (serializer already shipped the field) instead of a permanent false "response overdue".
5. **Partial index covers the sweep (PERF-H1).** `ih_email_active_sla_due` condition gains `"escalated"` (mig `0002` = RemoveIndex + AddIndex; verified safe on SQLite + PG16 — plain DROP/CREATE partial index; NOT concurrent — a zero-downtime prod deploy would want `AddIndexConcurrently`).
6. **Housekeeping:** module-level `from django.utils import timezone` in `services.py`.

**Test coverage** (`tests/test_inbox_hub_sla_response.py`, 25): `TestFirstRespondedStamping` (self-claim service + API claim + convert stamp; manager-routing does NOT; existing stamp not overwritten; auto-assign + dismiss do NOT), `TestTriagedEmailNoBreach` (self-claimed overdue → no breach; manager-routed + auto-assigned untouched → still breach + escalate), `TestEscalationCountGating` (re-escalate no-op; sweep no double-bump; RESOLVED no-op; API 400 on illegal escalate), `TestTerminalStateGuard` (cross-transitions → `ValueError` + 400 on BOTH API surfaces; stale-instance convert/dismiss rejected via the locked re-read; re-dismiss idempotent; re-convert returns existing ticket; deleted-ticket recovery re-convert), `TestSlaRiskLens` (lens = active + un-breached + un-responded only).

**Semantics note for future work:** in the Hub, "response" = *the assignee's own first action* (claim/self-assign/convert), NOT routing and NOT a customer-facing reply (the Hub has no reply action). If a reply action ever lands, revisit.

**Known low-severity residuals (round-2 verifier + pass-10 correction, deliberately deferred):** (a) `escalate_hub_email` + `transition_hub_email` still validate the caller's in-memory state with no locked re-read (`services.py:303-304,344-347`) — a racing committed convert/dismiss can be **stomped back to ESCALATED/IN_PROGRESS**, plus **duplicate side-effects** (two `EMAIL_ESCALATED` rows + two lead notifications). ⚠ **Pass-10 correction:** this is a **lost-update**, NOT counter "doubling" — `escalation_count = (self.escalation_count or 0) + 1` is computed on each request's in-memory instance (`:349`), so concurrent escalates collapse to a single net +1. ⚠ Also: **`select_for_update` is a NO-OP on the dev SQLite backend**, so even the guarded convert/dismiss/reassign terminal guards only truly serialize on prod Postgres. Window is one HTTP round-trip; the same lock-and-re-read pattern as convert/dismiss would close it. (b) The `except ValueError → 400` wrappers also catch non-terminal service validation errors (e.g. "No ticket statuses configured", reachable on fresh tenants) — truthful 400s, but the legacy non-hub branch of `InboundEmailViewSet.create_ticket` still 500s on the same error. (c) Nits: idempotent re-convert returns 201; a soft-deleted converted ticket does NOT trigger the recovery carve-out (FK nulls only on hard delete).

## Triage-Lens Removal + Auto-Assign Inbox Handoff (2026-07-02 — working tree, uncommitted)

The cockpit's **"Assigned to me" lens is REMOVED** (owner call: it duplicated the personal Inbox — `/inbox/` is the single "what's assigned to me" surface). The cockpit is now a pure triage desk: **4 lenses** (all-new / unassigned / oldest-waiting / self-hiding sla-at-risk). Because the lens was the ONLY place auto-assigned mail was visible (engine assignment never stamped `InboundEmail.assignee`, the field `/inbox/` filters on), removing it required **closing the auto-assign handoff gap** — otherwise engine-assigned mail would be visible to no one until SLA breach.

1. **`templates/pages/inbox_hub/list.html`** — `data-lens="mine"` button removed (explanatory template comment left in place); `inbox-hub.js` bumped **`?v=16`**.
2. **`static/js/inbox-hub.js` (1,482 LOC)** — all `mine` wiring removed: `state.counts.mine`, `els.countMine`, `LENS_LABELS.mine`, `EMPTY_LENS_MSG.mine`, the `buildListQuery` `case 'mine'` (`assignee=me`), and the counts fetch is now **3 requests** (`state=new` / `state=new&assignee=unassigned` / `sla_risk=true` — `results[2]` = sla).
3. **`apps/inbox_hub/assignment.py::assign_to`** — inside the existing atomic block, after the HubEmail save, stamps the **`InboundEmail` handoff** (`assignee=user`, `inbox_status=PENDING`, `is_read=False`) exactly like `reassign_hub_email`; **deliberately does NOT stamp `first_responded_at`** (engine assignment is not a response — the SLA durability layer is untouched). Covers both `try_assign` (park-time) and `drain_department_backlog` (agent-reconnect) since both route through `assign_to`. `_notify_assignment`'s click-through url changed **`/emails/` → `/inbox/`** (the cockpit can no longer show assigned mail); `_notify_hold` stays `/emails/` (held mail is still untriaged). Module docstring corrected (assign_to is engine-only; the manual API uses `reassign_hub_email`).
4. **`apps/inbox_hub/migrations/0003_backfill_inbound_assignee_handoff`** (data migration, applied to dev db.sqlite3) — one-off `RunPython` (noop reverse) that stamps the `InboundEmail` handoff (`assignee`/`inbox_status=pending`/`is_read=False`) for **pre-existing** non-terminal HubEmails with `assignee__isnull=False, inbound__assignee__isnull=True` — the rows auto-assigned *before* the `assign_to` fix, which the lens removal would otherwise strand on no surface. Uses `.update()` (no `save()`, so InboundEmail's linked/actioned immutability guards can't trip); excludes CONVERTED_TO_TICKET/DISMISSED; never overwrites an inbound that already has an assignee. **Verified live:** the orphaned-active-assigned count went 1 → 0.
5. **Tests** — `tests/test_inbox_hub_routing_assignment.py` +2: `test_auto_assign_hands_off_to_personal_inbox` (assignee + PENDING + is_read flip) and `test_auto_assign_does_not_stamp_first_responded` (SLA durability preserved).

**Line-shift note:** the handoff insert moved `_candidate_user_ids`' raw-role site `assignment.py:228` → **`:246`** (all references updated in this doc).

**Known deferred residuals (adversarial review, owner chose backfill-only 2026-07-02):** (a) the assignee-targeted SLA-breach / escalation notifications (`inbox_hub/tasks.py::_notify_sla`, `services.py::_notify_escalation`) still deep-link **`/emails/`** where — post-lens-removal — the assignee can't see their own mail; only `_notify_assignment` was redirected to `/inbox/`. (b) **No completion path** clears `InboundEmail.inbox_status` out of PENDING on convert/dismiss, so worked/dismissed hub-assigned mail lingers in `/inbox/` and keeps the nav Inbox badge inflated (partly pre-existing — `reassign_hub_email`'s manual handoff already had this; the auto-assign stamp widens it). `emails/list.html` also loads `?assigned=me` page-1-only (no pagination), so >50 assigned rows silently truncate.

## Mobile Triage Pass (2026-07-02 — working tree, uncommitted; 3-lens adversarially verified)

Fixes the OTHER cockpit review blocker: **below 992px the lens rail was `display:none` with no replacement** (users stranded on the active lens) and the stacked list+detail had no toggle. Files: `static/css/custom-v15.css` (**25,404 LOC**), `static/js/inbox-hub.js` (**1,513 LOC** — this pass layered with §Triage-Lens Removal; version chain `?v=13→14` [SLA badge] `→15` [this pass] `→16` [lens removal]), `templates/pages/inbox_hub/list.html` (**329 lines**). Verified by 3 refuter lenses (CSS cascade / JS interaction / UX+a11y) — all PASS; their findings (sticky action bar, doubled border, focus management, touch targets, empty-pane escape, live-yank toast, scroll restore) were folded in. Theme-check stays **137** (tokens only); JS `node --check` clean; braces balanced.

1. **Mobile lens chip bar.** At `<992px` the `.ih-nav` rail becomes a horizontal, swipeable chip bar — same DOM/JS, pure CSS restyle (pills with `--crm-radius-pill`, `min-height:40px` touch targets, hidden scrollbars, `.ih-nav-label`/`.ih-nav-foot` hidden). Every lens stays reachable at every width.
2. **One-pane-at-a-time list↔detail.** `.ih-detail { display:none }` by default; selecting a row adds **`ih-shell--detail-open`** on `#inboxHubShell` (hides nav+list, shows detail full-width). Cleared by: the **Back button** (44px, `#ihDetailBack` in the action bar + a second class-wired `.ih-detail-back--empty` in the empty/in-flight pane so a hung fetch never strands the user), Esc, triage completion (`showDetailEmpty`), detail-load failure, and **crossing ≥992px** (`resize`/`orientationchange` sync — a stale mobile class never survives a breakpoint crossing).
3. **Sticky triage actions while reading.** `ih-shell--detail-open` sets `.ih-shell { overflow: visible }` (the shell's `overflow:hidden` corner-clipping would defeat `position:sticky` — classic gotcha) and pins `.ih-actionbar` at `top: var(--crm-content-header-height)` so Back/Convert/Assign/Dismiss stay reachable on long emails (mobile scrolls the document, not the pane).
4. **A11y + polish (verifier findings):** focus moves into the pane on open (`showDetailView` → Back button, announcing the swap) and back to the `#ihListBody` listbox on close; `closeMobileDetail` re-anchors the selected row via `scrollIntoView` (a `display:none` list loses its scrollTop); the LiveBus "selected row left the lens" cleanup toasts *"That email was triaged by someone else"* when it yanks a mobile reader; `loadDetail` failure resets `selectedId`/`selectedDetail` + `renderList()` so c/a/x can't act on a stale email (also fixes a pre-existing desktop mismatch); mobile `.ih-list` border-bottom removed (doubled against the shell edge in one-pane mode); `aria-hidden` on the Back icons.

**Verifier residuals accepted as-is:** narrowing across the breakpoint mid-read lands on the list (safe default — selection kept, one tap restores); no chip-overflow edge-fade (chips fit ≥360px in the common case); a subsequent selection's in-flight window briefly shows the previous email (pre-existing race, mitigated by the failure-path reset). The messaging/chat page's older one-pane pattern has weaker a11y (unlabeled back button, 767.98px CSS vs `d-lg-none` mismatch) — worth backporting this pattern to chat later.

## Feature C — Agent-Decides Ticket Confirmation Email + Instant Reminder Scheduler (committed `6c6662d`)

### C1 — `auto_send_ticket_created_email` default flips True → False
- **`apps/tenants/models.py`** — `TenantSettings.auto_send_ticket_created_email` default changed `True → False`; help-text now says "When False (the default), agents decide per ticket and send it manually."
- **mig `tenants/0011_auto_send_ticket_created_email_default_false` [tracked/committed]** — `AlterField` (default False) + **`RunPython disable_auto_send`** that `.update()`s existing rows holding the old `True` default to `False` (forward-only; reverse = noop, to avoid silently re-enabling auto-send). Uses historical `apps.get_model("tenants","TenantSettings").objects`.
- **`apps/inbound_email/services.py::_create_ticket_from_email`** — the auto-send guard flipped from `if settings_obj is None or settings_obj.auto_send_ticket_created_email:` (send-by-default, even when settings missing) → **`if settings_obj is not None and settings_obj.auto_send_ticket_created_email:`** (send ONLY when the toggle is explicitly on). When off, it logs a note and does not `transaction.on_commit(...)` the confirmation task.
- **`templates/pages/settings/tenant.html`** — the `#autoSendCreatedEmailToggle` switch no longer renders `checked`.
- **`tests/test_email.py`** — `7.8b` (no auto-confirm by default; `send_ticket_created_email_task.delay` not called; ticket still created → `Status.TICKET_CREATED`) and `7.8c` (opted-in tenants still get it).

### C2 — `ReminderScheduler` (instant client-side reminder popup)
- **`static/js/app.js`** — a new `ReminderScheduler` IIFE alongside `ReminderAlerts`, started from the DOM-ready handler when `#notifDropdown` is present. It fetches the user's upcoming pending reminders (`GET /api/v1/crm/reminders/?mine=true&status=pending&page_size=200`), arms a precise `setTimeout` per reminder within a **6-minute horizon** (re-syncs every **5 min** and on `reminder.*` / `live.reconnected` LiveBus events), and calls `ReminderAlerts.show()` at the *exact* `scheduled_at` so the popup feels instant. The server's `fire_due_reminders` (30s Beat) is now the **backstop** (closed tabs / other devices). Recipient rule mirrors the server: `assigned_to` if set, else `created_by`.
- **`ReminderAlerts.show()` dedup** — keyed on `reminder_id@scheduled_at_epoch_ms` (6h memory bound) so the instant timer and the later WS notification never double-pop; a rescheduled reminder (new due-time) can still re-alert.
- **`window.Toast = Toast` published** at the end of `app.js` — `const Toast` lived only in module lexical scope, so other scripts feature-detecting `window.Toast` (inbox-hub.js, the Emails page) silently fell back to `console.log`/`alert`. Now surfaced everywhere. (`inbox-hub.js` defensively accepts `window.Toast || Toast`.)

## Emails / Inbox Rename + Cockpit Assignee Chip (committed `6c6662d`)

The two email surfaces were **relabelled and their frontend routes swapped**:
1. The **Inbox Hub** triage cockpit is now labelled **"Emails"** at **`/emails/`** (was `/inbox-hub/`; URL name `inbox-hub`→`emails`; legacy `/inbox-hub/` now **302-redirects** to `/emails/` via a `RedirectView`, name `inbox-hub-redirect`). Sidebar icon = `ti-mail`.
2. The old personal **"Emails"** page is now labelled **"Inbox"** at **`/inbox/`** (was `/emails/`; URL name `emails`→`inbox`). Sidebar icon = `ti-inbox`.

**Kept as-is (intentional name↔label mismatch):** view funcs (`inbox_hub_page` serves `/emails/`; `emails_page` serves `/inbox/`), template dirs (`pages/inbox_hub/`, `pages/emails/`), `inbox-hub.js`, the `inbox_hub` Django app, DOM/badge ids, and the **`/api/v1/inbox-hub/` API** (NOT renamed — `/api/v1/emails/` is already the inbound-email alias mount, so renaming would collide). The sidebar section header stays **"Inbox"**. ⚠ **The DOM badge ids are swapped relative to their labels:** the **Emails** link (→`/emails/`) carries `id="sidebarBadgeInboxHub"`; the **Inbox** link (→`/inbox/`) carries `id="sidebarBadgeEmails"` (`templates/includes/sidebar.html`) — a readability footgun inherited from the rename.

**Notification click-through URLs updated to match:** Hub triage notifications (`assign`/`hold`/`escalate`/SLA in `inbox_hub/assignment.py`, `inbox_hub/services.py::_notify_escalation`, `inbox_hub/tasks.py`) → **`/emails/`**; the personal-inbox handoff (`inbox_hub/services.py::_notify_reassignment`) → **`/inbox/`**. `page_back_button.html` and `nav/views.py` docstrings updated. **`inbox-hub.js` bumped `?v=10`→`?v=13`.**

**Cockpit assignee chip (NEW feature, not just a rename):** `inbox-hub.js` + `templates/pages/inbox_hub/list.html` + `custom-v15.css` add an **"Assigned to X / you / Unassigned"** indicator both per-row (`.ih-row-assignee`) and in the detail action bar (`#ihDetailAssignee` / `.ih-assignee-chip`). Previously a successful Assign looked like a no-op (the row left the lens, the pane showed nothing). The dashboard recent-activity rows were also restructured (`.activity-header` + `.activity-badge` with a `--act-status` CSS var instead of an inline `background-color`).

## Behaviour Reversals (committed `6c6662d` — the historical docs described the OPPOSITE)

### Agent ticket visibility — creator now KEEPS a ticket after handoff
- **`apps/tickets/access.py`** — `agent_visible_tickets_q(user)` is now `Q(assignee=user) | Q(created_by=user)` (was `Q(assignee=user) | (Q(created_by=user) & Q(assignee__isnull=True))`). `agent_can_see_ticket` is the OR equivalent. **A creator keeps the ticket in view even after assigning it to a different agent** — e.g. a triager who converts an inbound email and routes it to a teammate still finds it in their own list and detail. Admin/Manager (≤20) still bypass. Imported by `tickets/views.py`, `accounts/permissions.py` (`IsTicketAccessible`), `kanban/serializers.py`, `nav/views.py`, `analytics/services.py`, `attachments/access.py`.
- **`tests/test_access_control.py`** — `12.3b` inverted: `test_agent_created_ticket_visible_after_assigned_to_other` now asserts the creator STILL sees it in list + detail (200), and the new assignee sees it too.

### `TICKET_ASSIGNED` no longer emailed
- **`apps/notifications/services.py`** — `INTERNAL_ONLY_TYPES` gained `NotificationType.TICKET_ASSIGNED` (now **6 members**: TICKET_ASSIGNED, TICKET_OVERDUE, TICKET_FOLLOWUP_OVERDUE, REMINDER_DUE, REMINDER_OVERDUE, AGENT_STATUS_CHANGE). Ticket-assignment notifications now deliver **in-app/WS only** (`deliver_email` forced False), since they already surface as a live in-app notification — the emailed copy was redundant noise.

## Newsfeed 24h Auto-Expiry + Newsfeed/Notes RBAC (NEW — committed `6c6662d`, not in prior docs)

The headline `apps/newsfeed/views.py` change is a **24-hour auto-expiry**, plus the first regression coverage for the (pre-existing) newsfeed/notes RBAC rules:

- **Auto-expiry** — `NewsPostViewSet.perform_create` now defaults `expires_at = now() + 24h` when the author doesn't supply one (explicit `expires_at` is respected). `get_queryset` filters out expired posts: `Q(expires_at__isnull=True) | Q(expires_at__gt=now())`. `NewsPost.expires_at` field already existed (`models.py`); now surfaced in `NewsPostSerializer`.
- **Newsfeed RBAC** (`get_permissions`) — **create/update/delete = `[IsAuthenticated, IsTenantAdminOrManager]`** (Admin ≤10 / Manager ≤20 only; agents/viewers → 403). list/retrieve/react/mark_read/mark_all_read/unread_count = `[IsAuthenticated, IsTenantMember]` (any member). **Draft visibility:** non-admin (`effective_role.hierarchy_level > 20`) sees only `is_published=True`. Draft `post_save` does **not** `broadcast_live_event` (no live leak); publishing later fires `newsfeed.updated`.
- **Notes RBAC** (`apps/notes/views.py` — UNCHANGED code, newly tested): `QuickNoteViewSet` is `[IsAuthenticated, IsTenantMember]`, `get_queryset = QuickNote.objects.filter(user=request.user)`, `perform_create` stamps `user=request.user`. **Strictly per-user** — cross-user retrieve → 404.
- **`tests/test_newsfeed_notes_rbac.py`** (NEW, +59) asserts all of the above (auto-expiry default ~24h, explicit expiry honoured, expired hidden, draft-hidden-from-non-admins, draft-broadcast suppression, notes per-user 404).

## Sprint 0 Hardening (MERGED into `main`)

A 2026-06-14 end-to-end QA/security audit (`docs/qa-audit-2026-06-14/`, readiness **38/100** pre-fix) found **6 confirmed Criticals + no CI**. All 8 Sprint-0 launch-blockers are implemented and on `main`:

1. **DEBUG split-default → fixed + hardened.** `main/settings/__init__.py` defaults `DJANGO_DEBUG=False` → an unset var fails safe to `prod.py`; `main/checks.py::kanzen.E001` hard-fails the deploy check if `DEBUG is True`. CI sets `DJANGO_DEBUG="True"` to keep tests on dev.py.
2. **Messaging cross-tenant enumeration/injection → fixed (4 sites)** — user resolution membership-scoped in `mentions.py`, `consumers.py`, `views.py`, `ConversationCreateSerializer.validate`. Tests: `tests/test_messaging_tenant_isolation.py`. **⚠ NEW pass-10 hole:** the hardening covered the DIRECT/GROUP branches but **MISSED the TICKET-type conversation-create path** — `_create_ticket` `bulk_create`s participants from `data["user_ids"]` with no membership check (`serializers.py:413-417,597-607`), so a tenant-A member can inject a tenant-B user; a posted message then pushes a tenant-A preview to `notifications_{B-user}`, which `NotificationConsumer` (`consumers.py:39-64`, no membership check) delivers to the non-member. See pass-10 NEW findings.
3. **Attachment authz → fixed** — `attachments/access.py::can_access_target` + `CanAccessAttachmentObject` perm + authed `download/` action; raw `/media/` authed via `media_views.py::serve_protected_media`. Tests: `tests/test_attachment_authz.py`.
4. **Billing VoIP entitlement → fixed** — `seed_plans.py` per-plan flags; data mig `billing/0003`; `check_call_limit` permits Pro/Enterprise.
5. **Billing re-subscribe IntegrityError → fixed** — `webhooks.py::_sync_subscription_from_stripe` repoints the existing OneToOne row.
6. **IMAP cross-tenant dedup → fixed** — `imap_poller.py::_ingest_one` resolves tenant first, dedups on `filter(tenant, message_id)` (`imap_poller.py:362`).
7. **CI → added** (`.github/workflows/ci.yml`).
8. **Stale tests re-greened.**

> **Launch-fundamentals + QA-audit-followup passes (also on `main`):** API-register email-verify bypass closed; HSTS-preload; messaging add-participant tenant-None deny; Hub reassign terminal-guard; newsfeed draft-broadcast suppressed; **Company custom-field sync added**; Stripe webhook replay guard; attachment download/validator 404/400 (not 500); 3 unwired webhook events fired; **message-edit silent-no-op fixed**; comment-mention tenant leak; inbound FAILED rollback; ticket-create assignee authz; grant-temp-role admin block; dashboard manager-analytics gate; **internal-comment LiveBus redaction**; **Hub `first_responded_at` stamping**; escalate terminal no-op; partial `effective_role` drift cleanup.

## Multi-Tenancy Architecture

### Three-Layer Isolation

1. **TenantMiddleware** (`apps/tenants/middleware.py`): Resolves tenant from subdomain (`{slug}.localhost` / `{slug}.{BASE_DOMAIN}`), or `TenantSettings.domain` for custom domains. `_extract_slug` rejects nested sub-subdomains. Sets `request.tenant` and binds context. **`EXEMPT_PATH_PREFIXES` (17 entries):** `/static/`, `/media/`, `/api/v1/accounts/auth/`, `/api/v1/billing/plans/`, `/api/v1/billing/webhook/`, `/api/v1/tickets/csat/`, `/api/docs/`, `/api/schema/`, `/accounts/`, `/inbound/email/` (⚠ dead — no URLConf), `/login/`, `/register/`, `/logout/`, `/verify-email/`, `/verify-email-sent/`, `/setup-company/`, `/workspaces/`. **`/admin/` is NOT exempt** — a dedicated branch resolves the tenant (even to None) and defers access control to `SuperuserOnlyAdminSite`. A real-host lookup that resolves to no tenant → `JsonResponse(404)`. **`/auth/handoff/` intentionally NOT exempt.**

2. **TenantAwareManager** (`main/managers.py`): Default `objects` auto-filters by `get_current_tenant()`. Returns an **empty queryset** (`.none()`) when no tenant in context (fail-closed). Use `Model.unscoped` for cross-tenant queries. `SoftDeleteTenantManager` adds `is_deleted=False`.

3. **TenantScopedModel** (`main/models.py`): Abstract base. UUID PK + Timestamped + `tenant` FK (CASCADE, `editable=False`, db_index=True). `objects = TenantAwareManager()`, `unscoped = models.Manager()`. Overridden `save()` auto-assigns `tenant` from context; **raises `ValueError`** if no tenant bound and none provided. `TimestampedModel` = UUID PK + `created_at` + `updated_at`, default ordering `["-created_at"]`.

### Async-Safe Tenant Context (`main/context.py`)

```python
set_current_tenant(tenant); get_current_tenant(); clear_current_tenant()  # clear hard-sets None
with tenant_context(tenant): ...  # snapshots+restores previous (nesting-safe — preferred for tasks/consumers)
```
A single `contextvars.ContextVar("current_tenant", default=None)`. **`clear_current_tenant()` is NOT nesting-safe**; middleware uses it in `finally` (fine per-request). `WebSocketTenantMiddleware` resolves tenant from Host, sets `scope["tenant"]`, binds, clears in `finally`.

### Admin: jazzmin theme + Superuser Lock + Tenant-Filtered Mixin (`main/admin.py`)

- **`daphne`/`jazzmin` are INSTALLED_APPS entries 1–2.** Admin is jazzmin "darkly"-themed; `SuperuserOnlyAdminSite` reassigns `admin.site.__class__` at import (`has_permission` = `is_active AND is_superuser`). jazzmin does NOT override `has_permission`, so the lock holds.
- **`TenantFilteredAdmin` mixin** — `get_queryset` uses `model.unscoped.all()` filtered by `request.tenant`; `get_form` injects a `tenant` ModelChoiceField; `save_model` backfills `obj.tenant`. **`main/admin.py` registers NO models** — registration happens per-app.

### Palette (`apps/tenants/colors.py::derive_palette`)

`derive_palette(primary, accent)` returns a **24-key** dict of CSS custom-property values (`primary` + `primary_50…900` 10-step lightness scale via `colorsys` HLS + `primary_{hover,active,dark,light,subtle,ring,rgb}` + `accent` + `accent_{hover,light,rgb}` + WCAG-picked `text_on_primary`/`text_on_accent`; `logger.warning` if primary contrast < AA 4.5). ⚠ The **module docstring undercounts** ("17 primary + 3 accent" = 20) and **`accent_hover` is aliased to the raw `primary_hex`** (`colors.py:149`), not a derived accent shade. **Code defaults `#C1121F`/`#E11D2D`** (Crimson Black) — but `TenantSettings` *field* defaults are `#6366F1`/`#F59E0B`, so the palette-code defaults only kick in when the stored value is null/malformed. Wired via `context_processors.py` → `tenant_palette` → base.html `:root`.

## Settings Split, Redis, Ports

- `main/settings/__init__.py`: `from .base import *`, then `if env.bool("DJANGO_DEBUG", default=False)` → `from .dev import *` (try/except ImportError) **else** `from .prod import *`. Default fails safe to `prod.py`. **`main/checks.py::kanzen.E001`** hard-fails a deploy check if `DEBUG is True`.
- **Redis:** db3 cache + `cached_db` sessions (`KEY_PREFIX="kanzan"`); db4 Celery broker (result backend = `django-db`); db5 Channels (`prefix="kanzan:channels"`).
- **Sessions** `cached_db`, **host-only cookies** (no `Domain`); cross-host auth uses signed handoff tokens via `/auth/handoff/`.
- DRF auth order **JWT → APIKey → Session**; SimpleJWT access 15min/refresh 7d (rotate+blacklist, HS256, `JWT_SECRET_KEY` → falls back to `SECRET_KEY`). Allauth `ACCOUNT_LOGIN_METHODS={"email"}`; `django.contrib.sites` NOT installed. File upload caps 25MB. `django.contrib.postgres` installed (KB FTS).
- Prod media: `USE_X_ACCEL_REDIRECT` + `X_ACCEL_MEDIA_PREFIX` drive the `X-Accel-Redirect` path in `media_views.py`.

## Live Broadcast Layer

Unified pub/sub real-time layer: a per-tenant WebSocket fans server-side mutations to a client-side `LiveBus`. Coexists with per-domain consumers (chat, notifications, ticket-feed, presence, voip).

### Backend

- **`apps/tenants/live.py::broadcast_live_event(tenant, event, payload=None, *, immediate=False)`** — `tenant` may be a model instance OR a raw pk. Group: `live_tenant_{pk}`. Wire shape: `{type:"live_event", event:"<domain>.<verb>", payload:{...}, ts:ISO8601}`. Defers via `transaction.on_commit` (unless `immediate=True`); swallows `_send` exceptions (best-effort, logs).
- **`apps/tenants/consumers.py::LiveEventConsumer`** (group `live_tenant_{tenant_id}`). Anonymous/no-tenant → 4001; non-member → 4003. On `connect` stamps presence (`is_connect=True`); on `{action:"ping"}` re-stamps and replies `{type:"live.pong"}`. Presence NOT cleared on `disconnect` (reaper ages it out). The `live_event` handler re-shapes to `{type:<event name>, payload, ts}`.
- **`apps/tenants/routing.py`** → `re_path(r"ws/live/$", LiveEventConsumer.as_asgi())`.

### Signal Emitters

| App / source | Trigger | Verbs |
|-----|-----------|-------|
| `accounts` (`signals.py`, 5 receivers) | `TenantMembership.post_save/delete`, `Profile.post_save`, `User.post_save` (fans across active memberships) | `membership.created/updated/deleted`, `profile.created/updated`, `user.updated` |
| `comments` (2) | `Comment.post_save/delete` | `comment.created/updated/deleted` (✅ **internal `body` is `None` on the wire**) |
| `contacts` (8) | `Contact/Company/Account/ContactGroup × post_save/delete` (ContactEvent skipped; `last_activity_at` via `.update()` → no signal) | `contact.*`, `company.*`, `account.*`, `contact_group.*` |
| `crm` (4) | `Activity/Reminder × post_save/delete`. Reminder verb resolved by state (no `rescheduled` verb → reschedule emits `updated`) | `activity.*`, `reminder.created/updated/completed/cancelled/deleted` |
| `newsfeed` (4) | `NewsPost.post_save/delete`, `NewsPostReaction.post_save/delete` (draft-broadcast suppressed) | `newsfeed.created/updated/deleted`, `newsfeed.reacted` |
| `inbox_hub` (imperative — service/routing/assignment, NO signals.py) | park/route/assign/transition/escalate/reassign/convert/dismiss | `hub_email.created/transitioned/assigned/reassigned/escalated/converted_to_ticket/dismissed` |
| `agents`/`tenants` (presence) | `presence.handle_live_heartbeat`, `reap_stale_presence` task | `agent.presence` (broadcast `immediate=True`) |
| `crm` **task** (Feature A) | `fire_due_reminders` per due reminder | **`reminder.due`** (imperative, tenant-wide, payload incl. `recipient_id`) |

App configs (`apps/{accounts,comments,contacts,crm,newsfeed,tenants,tickets,voip,knowledge,custom_fields}/apps.py` + `notifications/signal_handlers.py`) import `signals` in `ready()` — **43 receivers total**. **`apps/inbox_hub/apps.py` has NO `ready()`**; no `signals.py`. **Tickets do NOT server-side broadcast to `live_tenant_*`** — `tickets/services.py::broadcast_ticket_event` publishes only to `ticket_feed_{tenant_id}`; the LiveBus bridge is **client-side** in `static/js/ticket-feed.js`.

### Frontend

- **`static/js/live-bus.js`** (175 LOC) — global `window.LiveBus`. API: `on/onMany/publish/debounce/rafBatch/isConnected/setChannelState`. Wildcard `"*"` gets all. Cross-tab fan-out via `BroadcastChannel('kanzan-live')`.
- **`static/js/live-connection.js`** (206 LOC) — global `window.LiveConnection`. Single shared `wss?://host/ws/live/`. Skips pre-auth pages / pages without a `sessionid` cookie. Backoff 1s→30s ±20% jitter, **infinite**. **25s heartbeat `{action:"ping"}`** with 8s pong timeout (drives server presence stamping). Publishes `live.reconnected` on reconnect; aggressive reconnect on tab visibility.
- **Wiring (`templates/base.html`)** — always: Bootstrap → DOMPurify → live-bus.js → api.js → app.js → command-palette.js → custom-select.js; then conditional on `tenant and user.is_authenticated`: live-connection.js → agent-availability.js → notes-panel.js → keyboard-shortcuts.js → ticket-feed.js → (if `voip_enabled`) SIP.js CDN + voip-softphone.js. **`inbox-hub.js` (`?v=13`) and `rich-editor.js` are page-specific** via `{% block extra_js %}`. `theme.js` loads synchronously in `<head>`. **Only `inbox-hub.js` is versioned.**

### Channel-Layer Groups & Close Codes

- `live_tenant_{tenant_id}`; `notifications_{user_id}`; `chat_{conversation_id}`; `ticket_feed_{tenant_id}`; `ticket_{ticket_id}_presence`; `voip_{tenant_id}`.
- `1000` clean; `4001` anon/no-tenant; `4002` invalid conversation UUID (chat); `4003` non-member; `4004` conversation tenant ≠ Host (chat). **`NotificationConsumer` and `CallEventConsumer` use a bare `close()`** for rejections.

## Feature A — Reminder-Due Popup (on `main`)

A "can't-miss" alert when a reminder comes due: a centered modal + Web-Audio chime + desktop/OS notification, delivered over `/ws/notifications/`. **Feature C** (`ReminderScheduler`, committed `6c6662d`) adds an exact-time client-side trigger on top.

### Data model + migration
- **`Reminder.due_notified_at`** (`apps/crm/models.py`, `DateTimeField(null, blank)`) — a **watermark, not a boolean**. Not indexed.
- **mig `crm/0005_reminder_due_notified_at`** — `AddField` + `RunPython suppress_existing_due_reminders` (reverse=noop): backfills `due_notified_at = F("scheduled_at")` for already-past-due AND active reminders so the first 30s tick does NOT pop the historical backlog. Uses `Reminder.objects` (historical manager has no `.unscoped`).

### Notification type
- **`NotificationType.REMINDER_DUE`** → NotificationType **21 members**. In **`INTERNAL_ONLY_TYPES`** (in-app/WS only, never emailed; now 6 members incl. TICKET_ASSIGNED). Reuses generic `send_notification(...)`.

### The task — `apps/crm/tasks.py::fire_due_reminders` (Beat `fire-due-reminders` @ 30s → kanzan_default)
- `@shared_task(bind, max_retries=1, acks_late)`. Per-tenant + per-row try/except; `Reminder.unscoped`.
- **Fire condition:** `scheduled_at <= now AND completed_at IS NULL AND cancelled_at IS NULL AND (due_notified_at IS NULL OR due_notified_at < F("scheduled_at"))` — the `< scheduled_at` half auto-re-arms after a forward reschedule.
- **Recipient** = `assigned_to or created_by` (`created_by` PROTECT → always a recipient).
- **Claim-first:** stamps `Reminder.unscoped.filter(pk=).update(due_notified_at=now)` **BEFORE** `send_notification`, so a downstream failure costs at most one *missed* alert, never a duplicate. `.update()` avoids re-tripping `post_save`. ⚠ No `select_for_update` (single-beat avoids the SELECT-then-UPDATE race).
- Emits `send_notification(REMINDER_DUE, data={reminder_id, contact_ids, priority, scheduled_at, url:"/reminders/"})` over `notifications_{recipient_id}` AND a tenant-wide `broadcast_live_event(tenant, "reminder.due", {...recipient_id...})` (drives the reminders-list refetch).

### Frontend
- **`static/js/app.js::ReminderAlerts`** IIFE (push-driven): in `initNotifications`, `if (data.type === 'reminder_due') ReminderAlerts.show(data); else showFlyout(data)`. `show()`: arms audio + `Notification.requestPermission`; Web-Audio two-tone chime (880/1174.66 Hz); desktop `Notification` (`requireInteraction:true`, `tag:reminder-<id>`, click → `/reminders/`); serializes a centered `#reminderDueModal`; degrades to a sticky Toast on pre-auth pages. **Now dedups** by `reminder_id@scheduled_at_epoch_ms` (Feature C). ⚠ `reminder_due` does NOT bump the sidebar Reminders badge.
- **`static/js/app.js::ReminderScheduler`** (Feature C, committed `6c6662d`) — exact-time instant trigger (see §Feature C).
- **`templates/base.html`** `#reminderDueModal` — `data-bs-backdrop="static"`. **There is NO `reminder-due-modal.js`** — the popup is entirely app.js-driven.
- **`templates/pages/reminders/list.html`** adds `'reminder.due'` to the LiveBus refetch subscription (debounced 500ms). `94468bc` also fixed a phantom scroll gap from closed flatpickr calendars (gated to `.open,.inline`).
- **Tests** (`tests/test_recalls.py::TestFireDueReminders`): fires once, future excluded, completed/cancelled excluded, creator fallback, re-arm on move-forward, suppressed-when-already-notified, internal-only, claim-first-prevents-refire.

## Feature B — Create Ticket From Email With Field Overrides (on `main`)

Upgrades email→ticket conversion from a one-click POST to a full **override form**. Both email→ticket surfaces share one validator and carry the full 9-field override set.

- **`apps/inbound_email/ticket_overrides.py::build_ticket_overrides(data, tenant)`** — single source of truth. Validates → raises **DRF 400 (not 500)**: `subject` ≤255, `description` ≤20000, `priority` ∈ `Ticket.Priority.choices` (case-insensitive), `category` ≤100, `queue`/`status` tenant-scoped PK lookup (malformed UUID → 400), **`status` rejects `is_closed`**, `assignee` must be an active `TenantMembership`, `due_date` parsed + made-aware, `tags` each ≤50. Blank/absent fields omitted so email-derived defaults survive.
- **`apps/inbound_email/services.py::_create_ticket_from_email(..., *, overrides=None)`** — overrides folded into the **initial `Ticket(**kwargs)`** (NOT a second `save()`), so `initialize_sla` runs against the final priority. Preserves the `"email"` provenance tag (deduped). **`_maybe_auto_assign` is SKIPPED when an explicit assignee override is supplied.** `overrides=None` → byte-identical legacy path.
- **`apps/inbox_hub/services.py::convert_to_ticket(...)`** — signature widened to the full 9 fields; passes a drop-None `overrides` dict into `_create_ticket_from_email` (SLA seeded against the right priority). Actor becomes ticket `created_by`.
- **Both API actions call the shared validator:** `InboundEmailViewSet.create_ticket` (`POST /inbound-email/{id}/create-ticket/`, role ≤30, idempotent → 400 w/ `ticket_number`) and `HubEmailViewSet.convert_to_ticket` (`POST /inbox-hub/hub-emails/{id}/convert-to-ticket/`). `ConvertToTicketSerializer` is schema-only (OpenAPI docs).
- **`templates/pages/emails/list.html`** — `#createTicketModal` form (subject* / priority / queue / status / assignee / category / description); `loadTicketMeta()` lazy-loads 4 dropdown sources; idempotent-400 treated as success.
- **`templates/pages/inbox_hub/list.html` + `static/js/inbox-hub.js` (`?v=13`)** — the cockpit "Convert" action opens a full **`#ihConvertPanel` offcanvas** (TipTap description + tags + flatpickr due-date + queue/status/assignee/priority/category).
- **Tests** (`tests/test_inbox_hub.py`): `TestConvertToTicketParity` + `TestHubConvertOverrides`.

## Feature — Inbound-Email Attachments (on `main`)

Surfaces + safely streams customer-sent email attachments on BOTH the Emails page and the cockpit. Inbound files are saved to `default_storage` and described in **`InboundEmail.attachment_metadata`** (JSONField list of `{filename, content_type, size, storage_path}`).

- **`apps/inbound_email/attachments.py`** — `serialize_attachments(inbound, url_for)` → renderable rows `{index, filename, content_type, size, is_image, url}` (URL **index-addressed** via `url_for(index)` — raw storage path never exposed). `stream_attachment(inbound, raw_index, *, force_download=False)` → `FileResponse`; bad/out-of-range index or missing file → **`Http404`**. **Safe raster images** (`png/jpeg/jpg/gif/webp/bmp` — `INLINE_IMAGE_TYPES`) served inline; **everything else (incl. SVG) forced to download**, always with `X-Content-Type-Options: nosniff`.
- **Authed download `@action` on BOTH viewsets:** `InboundEmailViewSet.attachment` (`GET /inbound-email/{id}/attachment/?i=<n>[&dl=1]`) and `HubEmailViewSet.attachment`. Each runs `get_object()` first so the surface's own permission stack gates the stream.
- **Serializers:** list rows carry `has_attachments` + `attachment_count`; detail adds the full `attachments` array with authed per-index URLs.
- **Tests:** `tests/test_inbound_email.py::TestInboundEmailAttachments` + `tests/test_inbox_hub.py::TestHubEmailAttachments`.

## Inbox Hub (full Phase-1B engine + triage cockpit + DEPARTMENT-scoped access — on `main`)

Email-to-Queue triage workspace. Reshapes inbound flow so NEW messages land in a centralised Hub for agent triage instead of auto-creating tickets, then routes to a department, seeds SLA deadlines, and auto-assigns to an online agent. **Default: OFF** — the seam at `apps/inbound_email/services.py` forks on `TenantSettings.inbox_hub_enabled` (default `False`).

> **Backend vs frontend split:** the *backend* is the full engine (routing, presence-aware assignment, hold/drain, state machine, SLA breach task, **10 `@action`s** incl. claim/escalate/transition/note + read-only `context` + authed `attachment` stream; `list`/`retrieve` come from mixins, not `@action`). The *frontend* (`inbox-hub.js` **1,482 LOC** `?v=16`, labelled **"Emails"**) is a **triage cockpit** surfacing **convert (full offcanvas) / assign / dismiss** over the untriaged backlog (**4 workload lenses** — the "Assigned to me" lens was REMOVED 2026-07-02, §Triage-Lens Removal; assigned mail is worked from the personal Inbox `/inbox/`) + per-email **customer-context card + attachment thumbnails + the assignee chip** — it does NOT call claim/escalate/transition/note.
>
> **⚠️ Access is DEPARTMENT-SCOPED** (the binary `UserGroup` gate is GONE; `user_in_any_group` deleted). `apps/inbox_hub/access.py` is the single source of truth (all gates use **`effective_role`**):
> - **`can_access_inbox_hub(membership, *, user, tenant)`** — Admin/Manager (`≤20`) → **always**; Viewer (`>30`) → **never**; agent-tier (Team Lead/Agent/IT/HR, 21–30) → granted iff (a) member of ≥1 **active** `Department`, OR (b) the tenant has **zero active departments** (fall-open safety valve), OR (c) they have ≥1 `HubEmail` assigned (black-hole safety valve).
> - **Row visibility** — supervisors (≤20) see all; agent-tier scoped by twin helpers: `hub_rows_q(user, dept_ids)` = `(state=NEW AND (department∈dept_ids OR department IS NULL)) OR assignee=me`, plus object-level `agent_can_see_hub_email`.
> - **Lockout surfaces:** hidden sidebar entry + zeroed badge (`nav/views.py`) + 403 on the page (`_inbox_hub_access_required`) + 403 on the API (`HubEmailPermission` + `IsHubEmailAccessible`).
> - **Rollout note:** on a tenant that already has departments, an agent who is NOT a Department member (and has no assigned mail) **403s** — make triagers Department members.

### The seam — `apps/inbound_email/services.py` (`process_inbound_email`)

```python
if existing_ticket:
    _add_reply_to_ticket(inbound, existing_ticket, contact, system_user)   # ALWAYS — replies never go to the Hub
else:
    settings = getattr(tenant, "settings", None)        # ⚠ misleading name (see footgun) — NOT a django.conf.settings collision
    if settings is not None and settings.inbox_hub_enabled:
        from apps.inbox_hub.services import park_email_in_hub
        park_email_in_hub(inbound, tenant, contact, system_user)
    else:
        _create_ticket_from_email(inbound, tenant, contact, system_user)   # (Feature B adds overrides=; Feature C gates the confirmation email)
```
**Existing-thread replies always go straight to the matching ticket.** **Footgun (readability only, NOT a bug):** the local `settings = getattr(tenant,"settings",None)` rebinds the *name* `settings` inside that function — but `apps/inbound_email/services.py` has **NO module-level `from django.conf import settings`** (it imports Django settings locally as `dj_settings`, `services.py:81,145`), so there is no actual settings-collision; it's a misleading-name hazard, not an active defect.

### Models (8 — `apps/inbox_hub/models.py`, 3 migrations: `0001_initial` + `0002_active_sla_index_covers_escalated` + `0003_backfill_inbound_assignee_handoff` [last two working tree])

- **`Department`** — `name`, `slug` (unique per tenant), `lead` (FK User PROTECT), `members` (M2M via `DepartmentMembership`), `default_queue` (FK Queue SET_NULL), `business_hours` (FK SET_NULL), `is_active`.
- **`DepartmentMembership`** — through-model. `skills` (JSON — **seeded-but-unused**).
- **`HubEmail`** — workspace entity, 1:1 `InboundEmail`. **9-state enum** (`NEW → ASSIGNED → IN_PROGRESS → PENDING_AGENT ⇄ AWAITING_CUSTOMER → ESCALATED → RESOLVED → CONVERTED_TO_TICKET | DISMISSED`) + **4-priority enum** (`low/normal/high/urgent` — **NO "medium"**; contrast Reminder HAS medium). SLA fields (`sla_response_due_at` indexed, `sla_resolution_due_at`, `response_breached`, `resolution_breached`, `first_responded_at` ✅stamped by `transition` + human assign/claim/convert [§SLA-Response Fix], `first_assigned_at`, `pause_started_at`/`total_pause_seconds` ⚠unused). Terminal: `converted_ticket` (1:1 Ticket SET_NULL), `dismissed_at`/`by`/`reason`. `auto_classification_data` JSONField. `tags` JSONField (**never written**). **5 indexes**.
- **`HubEmailAssignment`** — immutable audit. `Reason` enum (`AUTO/MANUAL/ESCALATION/REASSIGNMENT` — **ESCALATION never emitted**).
- **`HubEmailNote`** — internal note (`ordering=["created_at"]` ASC).
- **`HubEmailSLA`** — per-(queue, priority) or per-(department, priority) policy; `escalation_minutes` **unused**.
- **`RoutingRule`** — ordered IF/THEN. `match` JSON keys AND, values OR.
- **`QueueRouting`** — 1:1 supplement to `Queue`. `strategy_code` + `leave_unassigned_when_no_match` (**unused**).

### State machine (`state_machine.py`)

`can_transition(old,new)` False if equal; `assert_transition` raises `ValueError`. **Enforcement (post §SLA-Response Fix):** `transition_hub_email` asserts; `convert_to_ticket` + `dismiss_hub_email` now **assert too** (convert keeps the idempotent CONVERTED-with-ticket return + a deliberate recovery path for CONVERTED-with-deleted-ticket; both views map `ValueError`→400); `escalate_hub_email` no-ops unless `can_transition(old, ESCALATED)`. Only `assign_to`/`reassign_hub_email` still set `state` directly (NEW→ASSIGNED, always legal; reassign rejects terminal states explicitly).

### Engine

**`_post_park_hooks`** (on_commit, each step try/except-isolated): (1) `RoutingEngine.classify_and_route`, (2) `_initialize_hub_sla` (wall-clock deadlines), (3) `AssignmentEngine.try_assign` (only when `inbox_hub_auto_assign`, default True).

**`RoutingEngine`** (`routing.py`): `RoutingRule.unscoped.filter(tenant, is_active=True).order_by("order","id")`. Match keys AND / values OR; `sender_domain` exact-or-`.subdomain`; `recipient_local` exact; `keyword` substring (subject+body); `subject_regex` IGNORECASE (invalid → fail-closed + warn). **Empty `match` matches nothing.** Last-matched non-null outputs win; `stop_on_match` breaks. Fallback dept = `inbox_hub_default_department` (if active) else single active Department. Queue fallback = `department.default_queue`. Writes `EMAIL_CATEGORISED` (+`EMAIL_QUEUED`), broadcasts `hub_email.transitioned`.

**`AssignmentEngine`** (`assignment.py`): string-token strategies (`availability_aware → least_loaded → round_robin`). `_candidate_user_ids`: department members (or, if no department, `TenantMembership` at **`role__hierarchy_level == 30`** — ⚠ **raw role, not effective_role**, `assignment.py:246`), then `is_assignable`-filtered. `try_assign` — if none online → "held" + `_notify_hold` to the dept lead (url `/emails/`). `assign_to` — atomic `select_for_update`, concurrency guard, sets assignee/`first_assigned_at`/state→ASSIGNED, creates `HubEmailAssignment(AUTO)`, writes `EMAIL_AGENT_ASSIGNED`, broadcasts `hub_email.assigned`, **and (§Triage-Lens Removal, 2026-07-02) stamps the `InboundEmail` handoff** (assignee + `inbox_status=PENDING` + `is_read=False`, mirroring `reassign_hub_email`; deliberately does NOT stamp `first_responded_at`). `_notify_assignment` url → **`/inbox/`**. `drain_department_backlog` runs on agent (re)connect.

**Presence layer** (`apps/agents/presence.py` + `models.py` + `tasks.py`): `AgentAvailability.last_seen` (DateTime indexed, mig `agents/0007`). `DEFAULT_PRESENCE_TTL_SECONDS = 90`. **`is_assignable`** = `ONLINE` AND `remaining_capacity > 0` AND `presence_fresh` AND (if `auto_away_outside_hours`) within working hours (⚠ **server-local** time) — the single auto-assign gate. `touch_presence` get_or_create on `.unscoped`, auto-promotes OFFLINE→ONLINE only (gated by `AGENT_PRESENCE_AUTO_ONLINE`, default True). `handle_live_heartbeat` stamps + broadcasts `agent.presence` + drains backlog. `reap_stale_presence` (Beat 60s) flips stale ONLINE→AWAY.

**`apps/inbox_hub/tasks.py::check_hub_sla_breaches`** (Beat 120s; cross-tenant `.unscoped`) — sweeps active states (`NEW/ASSIGNED/IN_PROGRESS/PENDING_AGENT/ESCALATED`) with a response deadline. Flags response breaches (auto-escalates via `escalate_hub_email`), fires a one-shot warning `HUB_SLA_WARNING_MINUTES` (default 15) before deadline (deduped via `auto_classification_data["sla_warning_sent"]`), flags resolution breaches (flag-only). Notification url `/emails/`. **✅ (§SLA-Response Fix) `first_responded_at` is now stamped by the RESPONDER's first action** — claim/self-assign (`reassign_hub_email` when `actor.pk == new_user.pk`), convert, and `transition_hub_email` (RESPONSE_STATES) — so self-triaged mail no longer false-breaches. Deliberately NOT stamped by: manager routing-to-someone-else, engine auto-assignment (`assign_to`), or dismiss — routed/auto-assigned-but-untouched mail still breaches + auto-escalates (the durability layer).

### Service layer (`apps/inbox_hub/services.py`)

All write polymorphic `ActivityLog` rows + broadcast LiveBus on commit. **All 8 `EMAIL_*` ActivityLog actions + all 5 `HUB_EMAIL_*` Notifications emitted.**
- `park_email_in_hub` — idempotent `get_or_create(inbound=…)`; `EMAIL_RECEIVED`; broadcasts `hub_email.created` (immediate); schedules `_post_park_hooks`.
- `convert_to_ticket(...)` — idempotent (CONVERTED-with-live-ticket returns it); **`assert_transition` when not already CONVERTED** (→ `ValueError` from DISMISSED; view maps to 400; CONVERTED-with-deleted-ticket allowed back through as recovery); reuses `_create_ticket_from_email` (Feature B widened overrides); state→CONVERTED + **stamps `first_responded_at` if unset** (§SLA-Response Fix); `EMAIL_CONVERTED_TO_TICKET`. Actor becomes ticket `created_by`.
- `dismiss_hub_email(...)` — idempotent (re-dismiss no-op); **`assert_transition`** (→ `ValueError` from CONVERTED; view maps to 400); state→DISMISSED; does NOT stamp `first_responded_at` (a discard is not a response); `EMAIL_DISMISSED`.
- `transition_hub_email(...)` — `assert_transition`; **stamps `first_responded_at` on entry into a RESPONSE_STATE** (`IN_PROGRESS/PENDING_AGENT/AWAITING_CUSTOMER/RESOLVED`); `STATUS_CHANGED`; `hub_email.transitioned`.
- `escalate_hub_email(...)` — **(§SLA-Response Fix) full no-op unless `can_transition(old, ESCALATED)`** — terminal/resolved AND already-ESCALATED rows return unchanged (no count bump, no re-notify). On a genuine transition: `escalation_count += 1`, `escalated_to`=dept lead, state→ESCALATED; `EMAIL_ESCALATED`; `_notify_escalation` (url `/emails/`).
- `reassign_hub_email(...)` — `select_for_update`; online NOT required; rejects terminal states; **stamps `first_responded_at` on claim/self-assign only** (`actor is not None AND actor.pk == new_user.pk` — the RESPONDER acting, not a manager routing; engine auto-assign passes no actor; §SLA-Response Fix); `EMAIL_REASSIGNED`/`EMAIL_AGENT_ASSIGNED`. **Also stamps `inbound.assignee = new_user` + `inbox_status=PENDING` + `is_read=False`** — the agent email-inbox handoff backing `assign`/`reassign`/`claim`. `_notify_reassignment` url `/inbox/`.
- `add_hub_email_note(...)` — creates `HubEmailNote`; broadcasts `hub_email.transitioned`.

### API surface (`apps/inbox_hub/views.py` + `urls.py`)

`HubEmailViewSet` (list/retrieve only) permission stack `[IsAuthenticated, IsTenantMember, HubEmailPermission, IsHubEmailAccessible]`. **Agent-tier row filter (uses `effective_role`):** for `level > 20`, applies `hub_rows_q`. Chips: `assignee=me|unassigned|<uuid>`, `state`/`priority`/`queue`/`department`, **`sla_risk=true`**.

| Action | Method / URL | Codename | Service / note |
|---|---|---|---|
| `list`/`retrieve` | `GET /hub-emails/[{id}/]` | `hub_email.view` | — |
| **`context`** | `GET /{id}/context/` | `hub_email.view` | `contacts.context.build_contact_context` |
| **`attachment`** | `GET /{id}/attachment/?i=<n>[&dl=1]` | `hub_email.view` | `attachments.stream_attachment(obj.inbound, i)` |
| `convert_to_ticket` | `POST /{id}/convert-to-ticket/` | `hub_email.convert` | full 9-field override set via `build_ticket_overrides` |
| `dismiss` | `POST /{id}/dismiss/` | `hub_email.dismiss` | `dismiss_hub_email` |
| `assign` | `POST /{id}/assign/` | `hub_email.assign` | `reassign_hub_email` (validates member) |
| `reassign` | `POST /{id}/reassign/` | `hub_email.reassign` | `reassign_hub_email` |
| `claim` | `POST /{id}/claim/` | **none** — agent-level (≤30) | `reassign_hub_email(self)` |
| `escalate` | `POST /{id}/escalate/` | `hub_email.escalate` | `escalate_hub_email` |
| `transition` | `POST /{id}/transition/` | **none** — agent-level (≤30) | `transition_hub_email` (`ValueError`→400) |
| `note` | `POST /{id}/note/` | `hub_email.note` | `add_hub_email_note` → 201 |

**No `reply` action** despite the `hub_email.reply` codename. **`claim`/`escalate`/`transition`/`note` are live backend endpoints the cockpit never calls** (dead-but-present). **4 config viewsets:** `DepartmentViewSet` (+add/remove-members), `RoutingRuleViewSet` (+reorder, manager-gated), `HubEmailSLAViewSet`, `QueueRoutingViewSet`.

**`HubEmailPermission`** (`permissions.py`) — a **local** permission class (NOT the global `ACTION_MAP`). Uses `effective_role`. **First gate = `can_access_inbox_hub`**. Then `AGENT_LEVEL_ACTIONS = {claim, transition}` gated `≤30`; else per-action codename / hierarchy fallback: `view ≤40`, `convert/reply/escalate/note ≤30`, `assign/reassign/dismiss ≤20`. **`IsHubEmailAccessible`** delegates to `agent_can_see_hub_email`.

### RBAC (12 codenames — `apps/accounts/defaults.py` + mig `accounts/0012`) + department gate

`hub_email.{view, assign, reassign, convert, dismiss, reply, escalate, note}` (8) + `department.{view, manage}` (2) + `routing_rule.manage` (1) + `hub_sla.manage` (1). Grants: Admin/Manager = all 12; Team Lead = agent-tier + `assign/reassign/dismiss`; Agent/IT/HR = agent-tier; Viewer = `view` via ≤40 fallback. **The codename grants are SUBORDINATE to the department access gate.**

### Agent email-inbox handoff (`InboundEmail.assignee`)

- **`InboundEmail.assignee`** FK (SET_NULL, db_indexed, `related_name="assigned_inbound_emails"`, mig `inbound_email/0010`, **mutable**). Set by `reassign_hub_email` (assign/reassign/claim) **AND — since §Triage-Lens Removal (2026-07-02) — by `AssignmentEngine.assign_to` (auto-assign/drain)**, so every assignment path lands the mail in the agent's personal Inbox.
- **`apps/inbound_email/api_views.py`**: `InboundEmailViewSet.get_queryset` query-param branches — `?assigned=me`, `?internal=true`, `?mine=true`; default hides BOUNCED. The **`create_ticket` action** (`get_permissions` swaps to `[IsAuthenticated, IsTenantMember]`; handler enforces `effective_role ≤ 30`; idempotent → 400): if `email.hub_email` exists → `convert_to_ticket`; else `_create_ticket_from_email` (both via Feature B overrides).
- **`templates/pages/emails/list.html`** (now labelled **"Inbox"** at `/inbox/`): "Assigned to me" tab; dual-source load (`?internal=true&mine=true` + `?assigned=me`).

### Configuration & seeding

- **`TenantSettings`** Hub fields: `inbox_hub_enabled` (default **False**), `inbox_hub_auto_assign` (default **True**), `inbox_hub_default_department` (FK SET_NULL).
- **Settings constants** (`base.py`, env-overridable): `AGENT_PRESENCE_TTL_SECONDS=90`, `AGENT_PRESENCE_AUTO_ONLINE=True`, `HUB_SLA_WARNING_MINUTES=15`.
- **`manage.py seed_inbox_hub_defaults [--tenant-slug <slug> | --all-tenants]`** — seeds one "General" Department (idempotent). **Does NOT seed** RoutingRules/HubEmailSLA/QueueRouting.

### Known gaps / footguns (Inbox Hub)

- ~~**Auto-assign ≠ email handoff** — `assign_to` sets only `HubEmail.assignee`.~~ **✅ FIXED (§Triage-Lens Removal, 2026-07-02)** — `assign_to` now stamps `InboundEmail.assignee`/`inbox_status`/`is_read` like the manual paths.
- **Dead-but-present surface** — backend `claim`/`escalate`/`transition`/`note` + `hub_email.reply` codename unused by the cockpit (no longer SLA-load-bearing since the §SLA-Response Fix).
- ~~**`first_responded_at` rarely written in practice**~~ **✅ FIXED (§SLA-Response Fix, working tree)** — now stamped by claim/self-assign (responder acting), convert, and `transition`; manager routing, auto-assign + dismiss deliberately do NOT stamp. Regression tests: `tests/test_inbox_hub_sla_response.py` (25).
- ~~**`escalate_hub_email` re-escalation count bump**~~ **✅ FIXED (§SLA-Response Fix)** — escalate is a full no-op without a genuine transition into ESCALATED. `Reason.ESCALATION` still never emitted.
- ~~**⚠ Terminal-state bypass (pass 9)**~~ **✅ FIXED (§SLA-Response Fix)** — `convert_to_ticket` + `dismiss_hub_email` now `assert_transition` **against a `select_for_update` re-read** (concurrent convert+dismiss serialize; convert↔dismiss cross-transitions → `ValueError` → 400 on BOTH API surfaces incl. `InboundEmailViewSet.create_ticket`); idempotent re-convert/re-dismiss + the CONVERTED-with-deleted-ticket recovery path preserved.
- **Seeded-but-unused fields**: `DepartmentMembership.skills`, `HubEmail.tags`/`pause_started_at`/`total_pause_seconds`, `QueueRouting.leave_unassigned_when_no_match`, `HubEmailSLA.escalation_minutes`.
- **`InboundEmail.Status.PARKED_IN_HUB` IS written** — `park_email_in_hub` sets `inbound.status = PARKED_IN_HUB` and saves it (services.py:66-67). (The old "write-dead / keeps PROCESSING" note is OBSOLETE — re-derived fresh 2026-06-23.)
- **`_candidate_user_ids` no-department fallback uses raw `role`, not `effective_role`** (`assignment.py:246`). Also: `_default_department` returns None when ≥2 active depts exist and no `inbox_hub_default_department` is set → email parks with NO department → auto-assign falls to the raw-role tenant-agent pool.
- ~~**Partial active-SLA index gap (pass 6)**~~ **✅ FIXED (§SLA-Response Fix)** — mig `inbox_hub/0002_active_sla_index_covers_escalated` rebuilds `ih_email_active_sla_due` with `escalated` in the condition, matching the sweep's `active_states` exactly.
- Still future: business-hours-aware Hub SLA; `auto_classification_data` AI classification; historical backfill when the flag flips ON; auto-seed of a default Department; RoutingRule/HubEmailSLA/QueueRouting seeding.

## Models (91 model classes across 21 apps with models.py)

### Base Models (Abstract)
- **TimestampedModel**: UUID PK + `created_at` + `updated_at`; default ordering `["-created_at"]`.
- **TenantScopedModel**: TimestampedModel + `tenant` FK (CASCADE, editable=False, db_index=True) + auto-filtering.

### Tenants / Accounts

**tenants** (2): `Tenant` (name, slug unique, domain unique nullable, is_active, logo); `TenantSettings` (1:1; auth_method, SSO config, timezone, date_format, branding `#6366F1`/`#F59E0B` with hex validators, `inbound_email_address` unique, business hours/days, `auto_close_days` 5, `csat_delay_minutes` 60, `auto_transition_on_assign` True, **`auto_send_ticket_created_email` default False** [Feature C, was True], `auto_assign_inbound_email_tickets` default False, **`inbox_hub_enabled`** False, **`inbox_hub_auto_assign`** True, **`inbox_hub_default_department`** FK).

**accounts** (8): `User(AbstractUser)` (email-based, UUID PK, `username=None`, `auth_version`, `avatar`, `phone`, `is_service_account`). `Permission` — **global**; `Action` enum. ⚠ inbox-hub verbs (`convert/dismiss/reassign/reply/escalate/note`) NOT in `Permission.Action` but stored as the `action` value. `Role(TenantScopedModel)` — M2M `permissions`, `hierarchy_level` default 100, `is_system`. `Profile(TenantScopedModel)` (per-tenant prefs incl. free-text `department` string). `TenantMembership` — NOT TenantScoped; FKs `user`/`tenant`/`role` (PROTECT)/`temporary_role`/…; **M2M `temporary_permissions`**; methods `effective_role`, `get_effective_permissions_qs()`, `has_effective_permission()`. `Invitation`. `UserGroup` (M2M `members`; "one user per group" enforced; ⚠ NO LONGER gates the Hub — that's department-scoped now). `EmailVerificationToken` (global).

### Tickets (22)

`Pipeline`, `PipelineStage`, `TicketStatus` (`pauses_sla`, `is_closed`, `is_default`), `Queue` (`default_assignee`, `auto_assign`, **`department` FK → inbox_hub.Department SET_NULL**, mig 0027), `TicketCategory`, `TicketCounter` (NOT TenantScoped; `next_number()` SELECT-FOR-UPDATE — no-op on SQLite), `Ticket` (~80 fields; soft delete; CSAT; deal fields; `merged_into`; `pre_wait_status`; `tags`+`custom_data` JSON; **`save()` auto-populates `company_id`** from linked Contact; `clean()` validates assignee but **not** called by `save()`), `TicketLink` (4 types + circular guard), `SLAPolicy`, `EscalationRule`, `BusinessHours` (IANA tz + schedule JSON), `PublicHoliday`, `SLAPause`, `TicketActivity` (**27 `Event` choices** — inner class is `Event`, NOT `EventType`), `CannedResponse`, `Macro`, `SavedView`, `TicketAssignment`, `TicketWatcher`, `TimeEntry`, `TicketTemplate`, `Webhook` (HMAC SHA-256, 8 EventType, auto-disable at 10 failures).

### Contacts (5)

`Account` (`mrr`, `health_score` default 50, ⚠ clamped only in uncalled `clean()`), `Company` (name unique per tenant; `custom_data` JSON — ✅ **synced to CustomFieldValue** via `Company.post_save`), `Contact` (email unique per tenant, `email_bouncing` indexed, `lead_score` default 50 ⚠no clamp, `last_activity_at` written via `.update()` so no signal, `source` 6-choice), `ContactGroup` (M2M contacts), `ContactEvent` (append-only 360°; NOT live-broadcast; NOT in admin). `build_contact_context` in `context.py` (cache prefix `contact_context_v2`, 60s TTL) — shared by `ContactViewSet.context` and `HubEmailViewSet.context`.

### CRM (2)

`Activity` (call/email/meeting/task). `Reminder` (M2M `contacts`/`tickets`; `Priority` 4-choice incl. **MEDIUM** default — differs from HubEmail's no-medium; `status` is a derived property; custom `ReminderManager`/`ReminderUnscopedManager`; `mark_completed/mark_cancelled/reschedule`; **+ `due_notified_at`** watermark — Feature A).

### Inbound Email (3)

`InboundEmail` extends `TimestampedModel` (NOT TenantScopedModel — tenant nullable; default `objects` is a PLAIN manager). `Status` (**9 members** incl. `PARKED_IN_HUB` ✅now written by `park_email_in_hub`). **`assignee` FK** (SET_NULL, indexed, mutable, mig 0010). **`attachment_metadata` JSONField**. Threading: `message_id` (indexed, stored without `<>`), `in_reply_to`, `references`. Idempotency keys: `"in:{tenant}:{mid}"` / `"out:{tenant}:{ticket}:{mid}"` (unique). `save()` enforces immutability of `linked_at/by` + `actioned_at/by`. `BounceLog`, `IMAPPollState`. **Only InboundEmail in admin.**

### Knowledge (6)

`Category`, `Article` (status 5-choice; Postgres FTS via `SearchVectorField`+GinIndex — **dev no-op** on non-Postgres; **`allowed_groups` M2M to UserGroup**; **`tags` JSON IS live**; auto-slug; `save()` resolves tenant from context). `KBRevision` ⚠**dead-everything** (class def only), `KBVote`, `KBSearchGap`, `KBTicketLink` ⚠**dead-everything** (class def only — neither is ever instantiated, written, or queried). **Only Category + Article in admin.** ⚠ **`kb_search` (`search.py`) is Postgres-only — it builds a `SearchQuery`/`.filter(search_vector=…)` unconditionally with NO `icontains` fallback, despite the `KBSearchView` `@extend_schema` description (`views.py:546`) claiming "on SQLite falls back to `icontains`"** → `kb_search` raises on SQLite (dev). `ArticleViewSet.preview_file` returns raw (sanitised) HTML with an inline `<style>` block of hardcoded hex (theme-check doesn't scan it).

### Kanban (3)

`Board` (`resource_type` TICKET/DEAL, `is_default`, **`is_personal`**), `Column` (board, order, optional `status` FK, wip_limit), `CardPosition` (polymorphic GenericFK).

### Comments / Messaging / Newsfeed / Notifications

**comments** (4): `Comment` (polymorphic GenericFK, threaded, `is_internal`), `Mention`, `CommentRead`, `ActivityLog` (**34 action choices** — 26 core + 8 `EMAIL_*`). ✅ Comment broadcast **redacts internal bodies** (`body: None if is_internal`).

**messaging** (3): `Conversation` (DIRECT/GROUP/TICKET; FK `source_group`), `ConversationParticipant`, `Message` (`body` required at model level but blank-able in `MessageCreateSerializer`; null author = system; attachments via GenericFK). ✅ Cross-tenant user enumeration/injection fixed.

**newsfeed** (3): `NewsPost` (`author` CASCADE; `is_published`; **`expires_at`** — Feature: 24h auto-expiry default in the viewset), `NewsPostReaction`, `NewsPostRead`.

**notifications** (2): `Notification` (**21 NotificationType** — +5 `HUB_EMAIL_*` + `REMINDER_DUE`). **NOT polymorphic** — `data` JSONField + `recipient` FK. `NotificationPreference`. Single creator `send_notification(...)`; `INTERNAL_ONLY_TYPES` (**now 6** incl. TICKET_ASSIGNED) force email off.

### Agents / Custom Fields / Billing / Analytics / Attachments / Notes / API Keys / VoIP / Inbox Hub

**agents** (2): `AgentAvailability` (+`last_seen`/`presence_fresh`/`is_assignable`/`is_available`/`remaining_capacity`); `CustomAgentStatus`.
**custom_fields** (2): `CustomFieldDefinition` (8 types × **3 modules: ticket/contact/company**), `CustomFieldValue` (EAV, UUID `object_id`). ✅ Sync signals cover **Ticket + Contact + Company** (`signals.py:20/33/46`). ⚠ **Footgun:** each sync fires only when `custom_data` is a **non-empty dict** — clearing a populated `custom_data` to `{}`/`None` **skips the sync**, leaving orphaned `CustomFieldValue` rows that reporting/search still surface.
**billing** (4): `Plan` (global; `has_voip`/`has_call_recording`/`max_calls_per_month` — ✅ seeder sets them; mig `billing/0003` backfills), `Subscription` (1:1 Tenant; ✅ webhook repoints on re-subscribe), `Invoice`, `UsageTracker`. **No `tasks.py`.** `require_feature` 100% dead. `SubscriptionMiddleware` → 402.
**analytics** (4): `ReportDefinition`, `DashboardWidget`, `ExportJob` (⚠ PDF/XLSX-without-openpyxl → CSV bytes), `CalendarEvent`. **⚠ `DashboardView` IsAuthenticated only.**
**attachments** (1): `Attachment` (polymorphic, **UUIDField object_id**, python-magic MIME, 25MB). ✅ `access.py` object-level authz + `media_views.py` authed `/media/`.
**notes** (1): `QuickNote`. **api_keys** (1): `APIKey` (SHA-512; `kz_live_<slug6>_<token_urlsafe(32)>`).
**voip** (5): `VoIPSettings` (singleton; `is_active` drives softphone UI, decoupled from `Plan.has_voip`), `Extension`, `CallLog`, `CallRecording`, `CallQueue`.
**inbox_hub** (8): see §Inbox Hub.

### Polymorphic (GenericFK) Models — 5 total

`Attachment`, `Comment`, `ActivityLog`, `CustomFieldValue`, `CardPosition`. **Not** Notification.

## Role-Based Access Control

**Hierarchy:** Admin(10) → Manager(20) → **Team Lead(25)** → Agent(30) / **IT(30)** / **HR(30)** → Viewer(40).

**Default role seeding** (`apps/tenants/signals.py::create_default_roles`) runs on `Tenant.post_save (created=True)`, seeding **all seven** system roles inline; permission sets for the six perm-bearing roles come from `apps/accounts/defaults.py::ROLE_DEFINITIONS` (**6 entries** — Viewer permission-less, leans on ≤40 fallback). `ALL_CODENAMES`/`PERMISSION_DEFINITIONS` = **69 unique codenames** (12 inbox-hub-related; live DB has **71** rows — 2 orphans). `defaults.provision_default_roles` (the correct `unscoped` impl) is **dead**; permissions seeded by migrations `accounts/0011`/`0012`.

> **🐛 CONFIRMED BUG (reproduced live, pass 9) — `_assign_default_role_permissions` seeds ZERO permissions outside a tenant context.** It resolves each role via the tenant-scoped `Role.objects.get(tenant=…, slug=…)` (`signals.py:95`). When the tenant is created with **no tenant bound in context** — the `provision_tenant` management command, Django-admin on the bare domain, a shell, or any test factory — `Role.objects` fail-closes to `.none()`, so `.get()` raises `Role.DoesNotExist`, caught by `except … continue`. **Result: all 7 roles are created with 0 permissions** and the tenant runs purely on the `HasTenantPermission` hierarchy fallback. Verified against the live DB (re-reproduced pass 10): only the properly-context-bound `straat-x` has correct counts (admin 69 / manager 59 / team-lead 40 / agent 24 / it 25 / hr 25); all **9** other tenants show 0 across all roles. **⚠ Pass-10 correction:** the `Role.DoesNotExist` is swallowed by a **dedicated `except Role.DoesNotExist: continue` (`signals.py:96-97`)**, NOT the blanket `except (ImportError, Exception)` at `:41`/`:86` (those wrap only the `from apps.accounts.models import Role` imports; the write loop is outside them — the blanket tuple is still a redundant code smell but is not the masking mechanism). **Blast radius:** the entire fine-grained codename RBAC is inert (explicit-perm branch never reached → everything falls to the coarse hierarchy fallback); Team Lead (25) silently loses its intended delete/export/hub-assign grants (fallback needs ≤20); and `temporary_permissions` curation is voided (a temp-role allow-list gives full fallback-level access at the temp role's hierarchy). **Fix = `Role.objects.get` → `Role.unscoped.get`** (or wrap the assignment in `tenant_context(instance)`); the correct `unscoped` impl `defaults.provision_default_roles` already exists but is dead.

- `is_admin` ≤10; `is_admin_or_manager` ≤20; `is_agent_or_above` ≤30. **Team Lead (25)** satisfies `is_agent_or_above` but NOT `is_admin_or_manager`. Viewer (40) → ≤40 view-fallback only.
- **Always use `TenantMembership.effective_role`** (temp role wins until expiry; `temporary_permissions` non-empty = intersection). ⚠️ **Confirmed raw-`role` drift — 10 query sites (re-verified 2026-06-29):** the **4 load-bearing user-facing** ones — `agents/services.py:226` (`pick_email_agent`, `==30`), `inbox_hub/assignment.py:246` (`_candidate_user_ids` no-dept fallback, `==30`), `tickets/views.py:1052` (`teammates`, `≤30`) & `:1115` (`team_progress` candidate filter, `≤30`) — drive routing/visibility, so a temp-promoted agent gets object-perms + badges as a manager but is still email-routed / list-filtered as their raw role. The **6 others** select notification recipients / system actors (lower impact but still raw-role): `tickets/tasks.py:165` & `:329` (`≤20`), `knowledge/tasks.py:55` (`≤10`), `knowledge/views.py:223` (`≤20`), `crm/tasks.py:279` (`≤20`, inside the DEAD `check_overdue_reminders`), `inbound_email/services.py:234` (system actor `==10`).
- **`AgentAvailabilityViewSet.assignable_roles`** excludes the `admin` slug. `grant_temp_role` sets `temporary_permissions.set([...])`.
- **Shared visibility modules:**
  - **`apps/tickets/access.py`** — `agent_visible_tickets_q(user)` = **`Q(assignee=user) | Q(created_by=user)`** (a creator KEEPS a ticket after handing it off — no longer requires `assignee IS NULL`) + object-level `agent_can_see_ticket`. Imported by `tickets/views.py`, `IsTicketAccessible`, `analytics/services.py`, `nav/views.py`, `kanban/serializers.py`, `attachments/access.py`. Admin/Manager (≤20) bypass.
  - **`apps/inbox_hub/access.py`** — DEPARTMENT-scoped Hub gate (see §Inbox Hub).
  - **`apps/contacts/context.py`** — `build_contact_context` (cache `contact_context_v2`, 60s TTL).
- **Permission classes** (`apps/accounts/permissions.py`):
  - `HasTenantPermission` — codename-based; `ACTION_MAP` = **76 lines / 75 unique DRF-action keys** → `{resource}.{action}` (⚠ **duplicate key `"mark_all_read"`** at `permissions.py:46` & `:95` — both map to `"view"`, so benign, but a silent dict-key shadow). Uses `effective_role`; deny if `request.tenant is None`; no `permission_resource` on the view → **allow**; unmapped action → **deny**. Explicit-perm first (`get_effective_permissions_qs().exists()`), then hierarchy fallback (`view ≤40`, `create/update ≤30`, else `delete/manage/assign/export` ≤20).
  - `IsTicketAccessible` (object-level; ≤20 bypass).
  - `IsTenantMember` (⚠ returns `True` when `request.tenant is None`), `IsTenantAdmin`, `IsTenantAdminOrManager`.
  - **`HubEmailPermission` + `IsHubEmailAccessible`** — Inbox Hub's local stack.
- `_role_required(20)` gates admin/manager pages; `_role_required(30)` gates the personal Inbox. **`/settings/`** is `@_membership_required + @ensure_csrf_cookie` — any member loads; API enforces admin-only writes.

## Signals (10 apps with signals.py + notifications/signal_handlers.py — 43 receivers)

- **Tenants — 2:** `Tenant.post_save(created)` → `create_tenant_settings` + `create_default_roles`.
- **Accounts — 5:** `TenantMembership.post_save/delete`, `Profile.post_save`, `User.post_save`.
- **Tickets — 11:** `pre_save handle_ticket_status_change`; `post_save` × `fire_ticket_created_signal`, `fire_ticket_assigned_signal`, `log_ticket_activity` (**2-sec dedup**), `handle_sla_pause_on_status_change`, `create_kanban_card_on_ticket_save`, `sync_kanban_card_on_status_change`, `sync_kanban_card_on_pipeline_stage_change` (⚠ by column **name**); `post_delete remove_kanban_cards_on_ticket_delete`; `@receiver(ticket_closed) check_kb_article_coverage`; `SLAPolicy.post_save propagate_sla_policy_change`.
- **Custom Fields — 3:** `Ticket/Contact/Company.post_save` → sync `CustomFieldValue`.
- **Knowledge — 1:** `Article.post_save update_search_vector` (PG FTS; `.update()`; non-Postgres no-op).
- **Notifications — 2 (`signal_handlers.py`):** `ticket_assigned` (skips self), `ticket_comment_created` (+ mention parsing + contact-reply email).
- **VoIP — 1:** `CallLog.post_save` on terminal status.
- **Comments — 2:** `Comment.post_save/delete` (✅ internal bodies redacted).
- **Contacts — 8:** `Contact/Company/Account/ContactGroup × post_save/delete`.
- **CRM — 4:** `Activity/Reminder × post_save/delete`.
- **Newsfeed — 4:** `NewsPost/NewsPostReaction × post_save/delete`.

## Dual-Write Logging

1. **TicketActivity** — human-readable timeline, **27 `Event`s**. `/api/v1/tickets/tickets/{id}/timeline/`.
2. **ActivityLog** — polymorphic audit trail with diffs+IP, **34 actions**. `/api/v1/tickets/tickets/{id}/activity/`.

**Dedup pattern:** `log_ticket_activity` checks `instance._skip_signal_logging` (set in service sites + `perform_update`); a 2-sec window in `_activity_already_logged` is the safety net.

**Service layer** (`apps/tickets/services.py`, **20 public** functions of 27) — every mutation writes BOTH logs atomically + broadcasts via `on_commit`: `broadcast_ticket_event`, `initialize_sla`, `log_sla_change`, `create_ticket_activity`, `assign_ticket`, `validate_status_transition`, `transition_ticket_status`, `resume_from_wait`, `change_ticket_status`, `close_ticket`, `escalate_ticket`, `change_ticket_priority`, `log_ticket_comment`, `bulk_update_tickets`, `record_first_response`, `merge_tickets`, `split_ticket`, `render_macro`, `apply_macro`, `transition_pipeline_stage`. **⚠ NO `create_ticket()` service fn** — creation is serializer-driven in `TicketViewSet.perform_create`. **`ALLOWED_TRANSITIONS`** (slug-keyed); custom statuses transition freely.

**Kanban drags route through services** for cross-status drags (non-personal boards) — `apps/kanban/services.py::move_card` calls `tickets.services.change_ticket_status(...)`.

**⚠ Service-layer footguns (pass 9 + pass 10):** `bulk_update_tickets` "delete" action calls `ticket.delete()` (`services.py:1366`) — `Ticket` has **no `delete()` override**, so the bulk endpoint **HARD-deletes** while the single-ticket API path (`views.py:2364`) soft-deletes (irreversible bulk data loss; reachable by any Manager+ via `POST /tickets/tickets/bulk-action/`). `fire_ticket_assigned_signal` (`signals.py:162-170`) only emits `ticket_assigned` when `update_fields` includes `"assignee"` — a full `.save()` (`update_fields=None`) reassignment (jazzmin admin edit, shell, future path) sends NO assignment notification. `resume_from_wait`/`merge_tickets`/`split_ticket` query `TicketStatus.objects` with no explicit `tenant=` (`services.py:573,1570,1695`) — safe on-request (context bound) but a silent no-op off-request. **NEW pass 10:** `change_ticket_status` (`services.py:586-622`) does **NOT** call `validate_status_transition` (only `perform_update`/`change_status` action do), so **kanban `move_card` drags and `bulk_update_tickets` "change_status" perform UNVALIDATED transitions** — `ALLOWED_TRANSITIONS` is bypassable via those surfaces. `sync_kanban_card_on_status_change` (`signals.py:562-577`) re-moves the card to `Column…filter(status=new_status).first()` (lowest-order) — on a board with >1 column sharing a `TicketStatus` the user's **drop is silently relocated**; the pipeline-stage variant matches columns by **name `iexact`** (`:616-625`) so a column rename silently breaks that sync.

**Webhook service** (`apps/tickets/webhook_service.py`): `deliver_webhook` HMAC SHA-256, 10s timeout, auto-disable at 10 failures. 8 EventType members. **⚠ `fire_webhooks` dispatches via `.delay()`, NOT `on_commit`** (`services.py:142`) → a rolled-back transaction still fires the webhook; and the `kanzan_webhooks` queue is **consumed but has no producer** (`apps/billing/tasks.py` doesn't exist; ticket webhooks land in `kanzan_default`).

## SLA + Business Hours (`apps/tickets/sla.py`)

Single breach-detection entry `get_effective_elapsed_minutes()`: resolves per-tenant schedule via `BusinessHours` (JSON per-day + IANA tz) or legacy `TenantSettings` flat fields; skips `PublicHoliday`; subtracts pause duration. Falls back to **24/7 wall-clock** when no `BusinessHours`. `initialize_sla(ticket)` seeds deadlines.

> **Inbox Hub SLA is separate** — `_initialize_hub_sla` seeds **wall-clock** deadlines; `check_hub_sla_breaches` (Beat 120s) flags + warns + auto-escalates. No business-hours math.

## Inbound / Outbound Email

### Inbound (`apps/inbound_email/`)
- **In-process SMTP server** via `aiosmtpd` (`run_smtp_server`, PM2 `kanzan-smtp`, default `0.0.0.0:2525`). `handle_RCPT` rejects **550** if no tenant resolves; 25MB cap (**552**); optional STARTTLS + LOGIN/PLAIN auth.
- **IMAP poller** — shared mailbox; UID > watermark. Driven by `fetch_inbound_emails_task` (Beat 60s, `kanzan_email`). Disabled when `IMAP_HOST` blank. **Never backfills.** **✅ Dedup PER-TENANT** (`filter(tenant, message_id)`, `imap_poller.py:362` — tenant resolved FIRST).
- **Tenant resolution** — 4 strategies in `resolve_tenant_from_address`: plus-addressing → slug-as-local-part → `TenantSettings.inbound_email_address` → `IMAP_DEFAULT_TENANT_SLUG`.
- **Filters run BEFORE tenant resolution.** Bounces write `BounceLog` + flip `Contact.email_bouncing`.
- **Threading** — `find_existing_ticket` 3-tier: In-Reply-To → References → subject `[#N]`.
- **Processing pipeline** (`process_inbound_email_task`, max_retries=3, acks_late): `select_for_update` → filters → tenant resolution → idempotency claim → find/create contact → find existing ticket OR (per the seam) `park_email_in_hub` else `_create_ticket_from_email` → attach files → **confirmation email queued via `on_commit` ONLY if `auto_send_ticket_created_email` is True** (Feature C — was send-by-default).
- **Agent inbox workflow** (`inbox_services.py`): `link_email_to_ticket`, `action_email`, `ignore_email`.

### Outbound (`apps/tickets/email_service.py`)
- `send_ticket_email()` — single entry point. Skips undeliverable recipients (`.local`, RFC 2606); RFC Message-IDs; reply-to = tenant inbound address or `support+{slug}@{BASE_DOMAIN}`. Persists an OUTBOUND `InboundEmail` (`out:` key). Dev: `filebased.EmailBackend` → `tmp/emails/`. `_add_reply_to_ticket` does NOT fire `ticket_comment_created`; reopens resolved/paused tickets on customer reply.

## Auto-Assign (Inbound Email → Agent — legacy, distinct from Inbox Hub)

`apps/agents/services.py::pick_email_agent(tenant)`:
1. Active member with **`role__hierarchy_level == 30`** (Agent/IT/HR — ⚠ **raw role**, `services.py:226`).
2. Not OFFLINE; agents with no `AgentAvailability` eligible (fail-open). ⚠ Checks only OFFLINE, not `is_assignable`.
3. Fewest open tickets. 4. Tie-break least-recently-assigned (NULLS FIRST).

`auto_assign_email_ticket(ticket)` — gated by `TenantSettings.auto_assign_inbound_email_tickets` (default False).

## VoIP

Asterisk/FreePBX → ARI (REST + WebSocket Stasis). Django wraps ARI, exposes SIP creds to browser softphone (SIP.js over WSS), persists `CallLog`/`CallRecording`. **5 models** (`VoIPSettings` singleton `is_active` default True, `Extension`, `CallLog` [9 `Status` members], `CallRecording`, `CallQueue`).
- **`services.py`** — `check_call_limit` (the **only** `Plan.has_voip`/`max_calls_per_month` reader, called from one site: `InitiateCall` → 403; permits Pro/Enterprise), `process_ari_event` → `_broadcast_call_event` → `voip_{tenant_id}`. ⚠ **`Plan.has_call_recording` has ZERO readers anywhere → write-dead** (seeded + backfilled by mig `0003`, never consulted).
- **`consumers.py::CallEventConsumer`** (`ws/voip/events/`). ⚠ **No per-user scoping** — every tenant member joins `voip_{tenant_id}` and sees all call metadata. Bare `close()`.
- **`run_ari_listener`** — one listener per active tenant. **NOT in any PM2 config** — manual launch.
- **Softphone** — SIP.js 0.21.2 (CDN, conditional on `voip_enabled` = `VoIPSettings.is_active`, decoupled from `Plan.has_voip`).
- ⚠ **VoIP is structurally non-functional in the default PM2 runtime:** no `run_ari_listener` process **and** the `kanzan_voip` queue is unconsumed (worker `-Q` omits it), so all 3 voip tasks + Beat `cleanup-stale-calls` pile up — and the very cleanup task meant to FAIL stale ARI-less calls is itself stuck in that queue. Calls can be *initiated* over HTTP, but ARI events / recordings / stale-cleanup are all dead by default.

## API Architecture

### Authentication
- **API:** JWT (SimpleJWT) — 15min access, 7-day refresh, rotate+blacklist, HS256. **`APIKeyAuthentication`** (`Authorization: Api-Key kz_live_<slug6>_<secret>`). SHA-512, timing-safe `compare_digest`. Returns `None` (fail-open) when header absent; fails closed (401) on malformed/invalid/revoked/expired/cross-tenant.
- **`DEFAULT_AUTHENTICATION_CLASSES` order:** JWT → APIKey → Session.
- **Frontend:** Session auth (Redis cached_db, host-only cookie). **SSO:** django-allauth (Google, Microsoft, OIDC), email-only. **Global logout:** `User.auth_version` bump via `SessionVersionMiddleware`.

### `/api/v1/` Endpoint Map (23 router includes / 22 unique URLConfs — `inbound-email/` dual-mounts as `emails/`)

```
/tenants/      TenantViewSet, TenantSettingsViewSet (singleton; per-field Manager allowlist incl. inbox_hub_* toggles)
/accounts/     AuthViewSet (throttle "auth"), User, Role, Profile, Invitation, TenantMembership, UserGroup
/api-keys/     APIKeyViewSet (admin-only; mint/list/reveal-once/regenerate/revoke)
/tickets/      TicketViewSet (31 @action), TicketStatus, Queue, TicketCategory, SLAPolicy, EscalationRule, CannedResponse, Macro, SavedView, BusinessHours, PublicHoliday, TicketTemplate, Webhook, CSATSubmitView (public)
/contacts/     ContactViewSet (+context), Company, Account, ContactGroup
/billing/      PlanViewSet (AllowAny), SubscriptionViewSet (+cancel/reactivate), Invoice, Usage, checkout, webhook (CSRF-exempt, Stripe-signed)
/kanban/       BoardViewSet (+detail), Column, CardPositionViewSet (+move/reorder/add-ticket)
/comments/     CommentViewSet, ActivityLogViewSet (read-only)
/messaging/    ConversationViewSet (+add/remove/leave/search-participants), MessageViewSet (+broadcast author-only)
/notifications/  NotificationViewSet (+mark_read, unread_count, admin cleanup), NotificationPreferenceViewSet
/attachments/  AttachmentViewSet (multipart, true-MIME, cross-tenant validated, object-level authz)
/analytics/    DashboardView (APIView, ⚠ IsAuthenticated only), ReportDefinition, DashboardWidget, ExportJob, CalendarEvent
/agents/       AgentAvailabilityViewSet (grant/revoke_temp_role/assignable_roles excl admin), CustomAgentStatus
/custom-fields/  CustomFieldDefinition, CustomFieldValue (read-only)
/knowledge/    Category, ArticleViewSet (+submit_for_review/approve/reject/record_view/remove_file/preview_file/vote), KBSearchView
/notes/        QuickNoteViewSet
/inbound-email/  InboundEmailViewSet (read + create-ticket action; ?assigned=me/?internal=true/?mine=true; +attachment) + InboxViewSet (link/action/ignore)
/emails/       alias mount of inbound_email.api_urls (namespace="emails_api")
/crm/          ActivityViewSet (+my-tasks), ReminderViewSet (+overdue/stats/complete/cancel/reschedule/bulk-action; ?mine=true&status=pending), PipelineForecastView
/nav/          BadgeCountView (7 categories incl. inbox_hub; effective_role; capped at 99)
/newsfeed/     NewsPostViewSet (create/update/delete = Admin/Manager only; 24h auto-expiry default; drafts hidden from non-admins; +react/mark-read/mark-all-read/unread-count)
/voip/         VoIPSettings, Extension, CallLog (+active/stats), InitiateCall, CallHold, CallTransfer, CallHangup, SIPCredentials, CallRecordingDownload, CallQueue
/inbox-hub/    HubEmailViewSet (list/retrieve + 10 @actions) + Department + RoutingRule + HubEmailSLA + QueueRouting
```

**Non-HTTP inbound channel:** `kanzan-smtp` PM2 process (`0.0.0.0:2525`). **Docs:** `/api/docs/` (Swagger), `/api/schema/` (OpenAPI 3.0). **DRF config:** `DEFAULT_PERMISSION_CLASSES=[IsAuthenticated]`; throttles `[ScopedRateThrottle, APIKeyRateThrottle]`; rates `auth 10/min, api_default 200/min, api_heavy 30/min, webhook 60/min, api_key 1000/hour`; PAGE_SIZE 50.

### Public / unauthenticated endpoints
`POST /api/v1/tickets/csat/` (signed token), `GET /api/v1/billing/plans/` (`AllowAny`), `POST /api/v1/billing/webhook/` (HMAC, `@csrf_exempt`), `AuthViewSet.register/login/accept_invitation` (`AllowAny`, throttle `auth`).

### Frontend Routes (`apps/tenants/frontend_urls.py`) — 36 routes

```
/ login /register /logout /auth/handoff/ /verify-email/ /verify-email-sent/
/setup-company/ (login)  /workspaces/ (login)
/dashboard/ /tickets/ /tickets/new/ /tickets/<num>/ /contacts/ /contacts/create/ /contacts/<id>/
/calendar/ /kanban/ /messaging/ /analytics/ /knowledge/ /knowledge/<slug>/ /profile/ /api/quickstart/
/reminders/ /calls/ /inbound-email/ /audit-log/  (all @_membership_required)
/users/ /billing/ /agents/ /groups/  (@_role_required(20))
/inbox/  (@_role_required(30) — agent-level personal inbox; view func emails_page; labelled "Inbox"; was /emails/)
/settings/  (@_membership_required + @ensure_csrf_cookie — API enforces admin write)
/emails/  (@_inbox_hub_access_required — triage cockpit; view func inbox_hub_page; labelled "Emails"; was /inbox-hub/ → 403.html)
/inbox-hub/  (RedirectView 302 → /emails/ — legacy-bookmark bounce, name=inbox-hub-redirect)
```

> ⚠️ **Name↔label mismatch is INTENTIONAL** (committed `6c6662d`): `/emails/` → view `inbox_hub_page`, name `emails`; `/inbox/` → view `emails_page`, name `inbox`. Template dirs (`pages/inbox_hub/`, `pages/emails/`), the `inbox_hub` app, and the `/api/v1/inbox-hub/` API keep their original names.

## WebSocket Endpoints (6 total — `main/asgi.py`)

Stack: `ProtocolTypeRouter({"http": …, "websocket": AllowedHostsOriginValidator(AuthMiddlewareStack(WebSocketTenantMiddleware(URLRouter(messaging + notification + ticket + voip + live))))})`.

1. **Chat:** `ws/messaging/{conversation_id}/` → `ChatConsumer`. Group `chat_{id}`. 10KB/msg, 5 msg/s, 2s typing. Close 4001/4002/4003/4004. ⚠ `_create_message` hardcodes `"attachments": []`.
2. **Notifications:** `ws/notifications/` → `NotificationConsumer`. Group `notifications_{user_id}`. Bare `close()`. **The rail the reminder-due popup rides.**
3. **Ticket Presence:** `ws/tickets/{ticket_id}/presence/` → `TicketPresenceConsumer`. **Known gap:** `presence_list` for newcomers not implemented.
4. **Ticket Feed:** `ws/tickets/feed/` → `TicketListConsumer`. Group `ticket_feed_{tenant_id}`. Read-only.
5. **VoIP:** `ws/voip/events/` → `CallEventConsumer`. ⚠ Tenant-wide call metadata. Bare `close()`.
6. **Live:** `ws/live/` → `LiveEventConsumer`. Stamps presence on connect + each `ping`.

> `apps/inbox_hub/routing.py` is the **RoutingEngine** (email→department), NOT a Channels route.

## Celery Tasks & Beat Schedule

### Queue Routing (`main/celery.py` — 8 globs incl. default)
```
apps.billing.tasks.*                              → kanzan_webhooks   (DORMANT — apps/billing/tasks.py does not exist)
apps.notifications.tasks.send_email_* / send_notification_email → kanzan_email
apps.inbound_email.tasks.*                        → kanzan_email
apps.tickets.tasks.send_ticket_*                  → kanzan_email
apps.api_keys.tasks.send_api_key_*                → kanzan_email
apps.voip.tasks.*                                 → kanzan_voip       (⚠ no PM2 worker subscribes)
*                                                 → kanzan_default
```
**`CELERY_BEAT_SCHEDULE` lives in `main/settings/base.py`.**

### Beat Schedule (12 tasks)

| Beat key | Task | Schedule |
|----------|------|----------|
| `check-sla-breaches` | `apps.tickets.tasks.check_sla_breaches` | 120s |
| `check-overdue-tickets` | `apps.tickets.tasks.check_overdue_tickets` | 900s |
| `cleanup-old-notifications` | `apps.notifications.tasks.cleanup_old_notifications` | 86400s |
| `calculate-lead-scores` | `apps.crm.tasks.calculate_lead_scores` | 86400s |
| `calculate-account-health-scores` | `apps.crm.tasks.calculate_account_health_scores` | 86400s |
| `kb-stale-alert` | `knowledge_base.alert_stale_articles` | crontab daily 08:00 (UTC) |
| `kb-gap-digest` | `knowledge_base.send_gap_digest` | crontab Mon 09:00 (UTC) |
| `cleanup-stale-calls` | `apps.voip.tasks.cleanup_stale_calls` | 3600s (⚠ piles up — kanzan_voip unconsumed) |
| `fetch-inbound-emails` | `apps.inbound_email.tasks.fetch_inbound_emails_task` | 60s |
| `reap-stale-presence` | `apps.agents.tasks.reap_stale_presence` | 60s |
| `check-hub-sla-breaches` | `apps.inbox_hub.tasks.check_hub_sla_breaches` | 120s |
| `fire-due-reminders` | `apps.crm.tasks.fire_due_reminders` | 30s |

Celery Beat uses the **built-in shelve scheduler**. **`apps.crm.tasks.check_overdue_reminders` and `apps.tickets.tasks.check_sla_breach_warnings` exist but are NOT in Beat** (dead). 27 tasks total; 15 unscheduled.

## PM2 Processes — 5 prod / 4 dev

### `ecosystem.config.js` (prod, venv `.venv/`)

| Name | Purpose |
|------|---------|
| `kanzan-django` | `gunicorn main.asgi:application -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8001 --timeout 120` (2GB) |
| `kanzan-celery-worker` | `celery -A main worker -Q kanzan_default,kanzan_email,kanzan_webhooks -c 4 --max-tasks-per-child=200` (2GB) |
| `kanzan-celery-beat` | `celery -A main beat -l info` |
| `kanzan-flower` | `celery -A main flower --port=5556 --basic_auth=$KANZAN_FLOWER_AUTH` |
| `kanzan-smtp` | `manage.py run_smtp_server` (2525) |

> **⚠ Worker `-Q` = `kanzan_default,kanzan_email,kanzan_webhooks`** — `kanzan_voip` unconsumed (3 voip tasks + Beat `cleanup-stale-calls` accumulate). `run_ari_listener` **not in PM2**. **Makefile `stop`/`restart` omit `kanzan-smtp`.**

### `ecosystem.dev.config.js` (dev, venv `env/` → `.venv/`) — 4 processes
- `kanzan-django` `runserver`. `kanzan-celery-worker` `-c 2`, **watch** on `apps/*/{tasks,services}.py` + `main/celery.py`. `kanzan-celery-beat`, `kanzan-flower`. **No `kanzan-smtp`.**

## Frontend Architecture

### JavaScript (`static/js/`, 14 modules, 5,934 LOC working — vanilla, no React/Vue)

| Module | LOC | Role |
|--------|----:|------|
| `inbox-hub.js` | **1,513** | Triage COCKPIT (`?v=16`, labelled "Emails"): **4 lenses** (all/unassigned/oldest/sla — "Assigned to me" REMOVED, §Triage-Lens Removal), **mobile chip bar + one-pane list↔detail swap w/ focus management** (§Mobile Triage Pass), 3-count fetch, customer-context card, **assignee chip**, attachment thumbnails, SLA badge (short-circuits to "responded" on `first_responded_at` — §SLA-Response Fix), full `#ihConvertPanel` offcanvas (TipTap + tags + flatpickr) / assign / dismiss, J/K/C/A/X/Esc, 7 LiveBus subs (400ms). NO claim/escalate/transition/note UI |
| `app.js` | **1,288** | Global init: alerts, sidebar (+ close-on-widen ≥992px — Pass 4), notification WS, Toast (now `window.Toast`-published), `Kanzan.formatDate/…`, badges, live-status pill, **`ReminderAlerts` IIFE** (Feature A) + **`ReminderScheduler` IIFE** (Feature C) |
| `voip-softphone.js` | 710 | SIP.js 0.21.2 + `CallEventConsumer` |
| `custom-select.js` | 371 | `KanzenSelect` portal-rendered styled selects |
| `command-palette.js` | 337 | Cmd+K modal (⚠ "New Contact" → dead `/contacts/new/`) |
| `keyboard-shortcuts.js` | 318 | Global hotkeys; injects runtime `<style>` |
| `ticket-feed.js` | 248 | `ws/tickets/feed/` → republishes into LiveBus |
| `agent-availability.js` | 244 | Status toggle + `subscribePresence()` |
| `notes-panel.js` | 238 | Quick notes CRUD |
| `live-connection.js` | 206 | Single shared `ws/live/`, 25s heartbeat / 8s pong, backoff 1s→30s (infinite) |
| `rich-editor.js` | 191 | TipTap wrapper |
| `live-bus.js` | 175 | Global pub/sub `window.LiveBus` (BroadcastChannel cross-tab) |
| `api.js` | 90 | Central API client (CSRF cookie + meta fallback) |
| `theme.js` | 77 | light/dark/system (default dark). Loaded SYNCHRONOUSLY in `<head>` |

> ⚠ Multiple independent notification-WS backoffs: `app.js initNotifications` (max **10**) vs `live-connection.js` (**infinite**) vs `ticket-feed.js` (10) vs `voip-softphone.js` (5s fixed). Only `inbox-hub.js` is cache-busted (`?v=13`).

### CSS & Theming

- **`static/css/custom-v15.css`** — **25,330 LOC** working tree (25,246 committed + ~84 from the 5 uncommitted UI passes), "Design System v9.0 Crimson Black" (the only loaded project CSS). Token scales: `--crm-radius-{xs:4,sm:8,md:6,lg:14,xl:16,pill:9999}` (⚠ `md`<`sm` quirk; pill has 70 uses, all raw 999/100px radii migrated), `--crm-space-{0…12}` (full 0px→48px spacing scale; ⚠ **0 uses** across ~1,900 raw padding/margin/gap), `--crm-text-{2xs:10…3xl:24}` (incl. `--crm-text-2xs`), `--crm-weight-{normal:400,medium:500,semibold:600,bold:700}`, **`--crm-leading-{tight:1.2,snug:1.35,normal:1.5,relaxed:1.65}`** (Pass-2 line-height scale — ⚠ **0 uses**, dead), `--crm-z-{base:1,sticky:10,dropdown:1000,modal-backdrop:1040,modal:1050,popover:1060,tooltip:1070,flyout:1080,overlay:1085,toast:1090}`, `--crm-accent-rgb: 225,29,45` (**79** `rgba(var(--crm-accent-rgb),a)` uses), **`--crm-primary-rgb: 193,18,31`** (Pass-2 self-contained `:root` fallback mirroring the per-tenant runtime palette in `base.html`, **213** `rgba(var(--crm-primary-rgb),a)` uses), **`--crm-avatar-bg`/`--crm-avatar-fg`** (theme-reactive identity-avatar tokens; light bg=`--crm-primary`, dark bg=`--crm-primary-hover`), + duration/easing tokens. **Pass-9 5th-refinement additions:** `.crm-page-content.fade-in` + `kz-fade-up` shell-sweep page-entrance layer (`:~22634-22803`) + `.fd-trends-canvas-wrap { height:200px }` Chart.js fix + `.nf-carousel__track` height-transition. Includes Inbox Hub cockpit + `.ih-convert-*` offcanvas + `.ih-assignee-chip`/`.ih-row-assignee` + `.activity-header`/`.activity-badge` (`--act-status`) + `.activity-list`/`.activity-item` (`--activity-row-h: 4rem`, max-height = 5 rows) + `.reminder-due-*` blocks (token-only).
- **`static/css/custom.css`** — 20,431 LOC committed snapshot, NOT loaded, allowlisted.
- **No hex literals in rule bodies.** `make theme-check` enforces against `scripts/.theme_baseline.json` (**baseline 145 hex / 10 files**). **PASSES** — runtime reports **"137 pre-existing hex literals tracked"** (re-confirmed 2026-07-01) because the uncommitted UI passes tokenized several literals (`contacts/list.html` 5→0, `verify_email_sent.html` 3→0) while the baseline JSON still records their old counts (a stale-high ceiling — see footguns). The old `base.html` toast inline `z-index:1090` footgun is **closed** (inline style removed; now `.toast-container` token-driven).

### Templates (48 .html files)

- `templates/base.html` (318 lines working / 292 committed) — palette `<style>`, **mobile-class detection via pure `window.innerWidth < 992`/`<576` `applyMobileClasses()`, re-evaluated on resize/orientationchange** (5th refinement — touch heuristic removed), body-level **`{% block overlays %}`** (offcanvas-backdrop stacking fix, uncommitted), toast container, quick-notes panel, **`#reminderDueModal`** (static backdrop), softphone (conditional), DOMPurify 3.2.4 + SIP.js 0.21.2 CDN + Flatpickr loader + synchronous `kanzan_sidebar_collapsed` pre-paint. Default theme: dark.
- `templates/includes/` (6 files): `navbar.html`, `sidebar.html` (Inbox section gated `{% if can_access_inbox_hub %}`; **Emails**=`ti-mail`→`/emails/`, **Inbox**=`ti-inbox`→`/inbox/`), `softphone.html` (conditional), `messages.html`, `page_back_button.html` (included by **18**), `kb_sidebar_widget.html` (**ORPHAN**).
- `templates/pages/` — **18 subfolders + 8 root files** (403, api_quickstart, calendar, dashboard, landing, login, profile, register).
- Notable: `reminders/list.html` (split-pane, NL quick-add, `reminder.due` LiveBus, flatpickr scroll-gap fix); `emails/list.html` (now titled **"Inbox"**; `#createTicketModal`, dual-source load); `inbox_hub/list.html` (now titled **"Emails"**; `#ihConvertPanel`, assignee chip, `inbox-hub.js ?v=13`); `settings/tenant.html` (2 Inbox Hub toggles + the auto-send toggle [Feature C: now unchecked by default]); `dashboard.html` (restructured recent-activity rows: `.activity-header` + `.activity-badge`); `audit_log/list.html`; `tickets/list.html` (dynamic per-status tabs); `tickets/detail.html` (Delete-Ticket removed; macro JS dead no-ops).

### Context Processor (`apps/tenants/context_processors.py`)

Injects: `tenant`, `membership` (cached on `request._cached_tenant_membership`), `user_role` (= `effective_role`), `is_admin`/`is_admin_or_manager`/`is_agent_or_above`, **`can_access_inbox_hub`**, `voip_enabled` (= `VoIPSettings.is_active`), `tenant_palette` (~24-key), `BASE_URL`.

## Middleware Stack (14 layers)

1. SecurityMiddleware 2. WhiteNoiseMiddleware 3. SessionMiddleware 4. CorsMiddleware 5. CommonMiddleware 6. CsrfViewMiddleware 7. AuthenticationMiddleware 8. AccountMiddleware (allauth) 9. **SessionVersionMiddleware** (global logout via `auth_version`) 10. **TenantMiddleware** (`/admin/` dedicated branch; after auth) 11. **SubscriptionMiddleware** (402 when neither active nor in grace) 12. **RateLimitHeadersMiddleware** 13. MessageMiddleware 14. XFrameOptionsMiddleware

## Billing Plans (⚠ FROM THE SEEDER — `seed_plans.py`)

| Plan | $/mo | Users | Contacts | Tickets/mo | Storage | VoIP | Recording | Calls/mo |
|------|---|-------|----------|-----------|---------|------|-----------|----------|
| Free | 0 | 3 | 500 | 100 | 1GB | No | No | 0 |
| Pro | 29 | 25 | 10K | 5K | 25GB | Yes | Yes | 1000 |
| Enterprise | 99 | ∞ | ∞ | ∞ | ∞ | Yes | Yes | ∞ (None) |

> **✅ VoIP entitlement seeded (Sprint 0); mig `billing/0003` backfills existing rows.** `check_call_limit` permits Pro/Enterprise. Softphone *UI* visibility still gated by `VoIPSettings.is_active` (decoupled from `Plan.has_voip`). `require_feature("voip")` decorator still 100% dead.

## Management Commands (8 total)

```bash
python manage.py provision_tenant --name "Acme" --slug acme [--domain crm.acme.com]
python manage.py seed_plans                                    # Free/Pro/Enterprise (sets VoIP flags)
python manage.py setup_queues --tenant-slug demo
python manage.py setup_ticket_statuses --tenant-slug demo
python manage.py backfill_sla_audit [--tenant-slug] [--dry-run]
python manage.py seed_inbox_hub_defaults [--tenant-slug <slug> | --all-tenants]  # General dept only
python manage.py run_smtp_server                               # kanzan-smtp PM2 process
python manage.py run_ari_listener                              # VoIP Stasis event loop (NOT in PM2)
```

## Environment Variables

- **`.env.example` (16 keys):** `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DATABASE_URL`, `REDIS_URL`, `BASE_DOMAIN`, `BASE_SCHEME`, `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`, `JWT_SECRET_KEY`, `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `KANZAN_FLOWER_AUTH`.
- **base.py reads 40 env keys; 25 are read-but-undocumented** (in base.py, absent from `.env.example`): `AGENT_PRESENCE_AUTO_ONLINE`, `AGENT_PRESENCE_TTL_SECONDS`, `BASE_PORT`, `DEFAULT_FROM_EMAIL`, `EMAIL_BACKEND`, `EMAIL_TIMEOUT`, `EMAIL_USE_SSL`, `HUB_SLA_WARNING_MINUTES`, `INBOUND_EMAIL_WEBHOOK_SECRET`, `USE_X_ACCEL_REDIRECT`, `X_ACCEL_MEDIA_PREFIX`, 5×`IMAP_*` (`IMAP_DEFAULT_TENANT_SLUG`, `IMAP_HOST`, `IMAP_MAILBOX`, `IMAP_PASSWORD`, `IMAP_PORT`, `IMAP_USER`, `IMAP_USE_SSL`), 7×`SMTP_SERVER_*` (`SMTP_SERVER_AUTH_USERS`, `_HOST`, `_HOSTNAME`, `_PORT`, `_REQUIRE_AUTH`, `_TLS_CERT_FILE`, `_TLS_KEY_FILE`).
- **`KANZAN_FLOWER_AUTH` is documented but NOT read by `base.py`** — consumed only by `ecosystem.config.js`.

## Testing

- **Framework:** pytest + pytest-django. **75 modules** (68 root `tests/test_*.py` + 7 app-level: `apps/tickets/tests/` ×2, `apps/api_keys/tests/` ×4, `apps/knowledge/tests/` ×1). `pytest.ini`: 2 config directives (`DJANGO_SETTINGS_MODULE=main.settings`, `pythonpath=.`, **no `asyncio_mode`** → defaults `strict`; `dev.txt` ships `pytest-asyncio`).
- **Fixtures (`conftest.py`, 343 LOC):** **16 factories + 20 fixtures** (3 autouse: `celery_eager`, `free_plan`, `clear_tenant_context`). `ReminderFactory` sets `priority="medium"` + `scheduled_at=now()`.
- **✅ FULL SUITE GREEN — re-verified 2026-07-10 (pass 12, working tree with the security + go-live batch):** `python -m pytest -q` = **1120 passed / 0 failed / 22 skipped / 1 xfailed** (221.55s on SQLite; **1143 collected** — the +42 vs pass-11 are `test_bulk_ticket_delete` + `test_go_live_fixes` + `test_rate_limiting` + additions to `test_recalls`/`test_messaging_tenant_isolation`/`test_notifications`/`test_inbox_hub_routing_assignment`). The 22 skips are env-gated (Postgres-only FTS etc.); the 1 xfail expected. `makemigrations --check` clean; `manage.py check` clean; `make theme-check` green (live **137** vs baseline 145/10); `ruff check .` = 198 lint issues (non-blocking, +1 from the new working-tree files). (Committed `df9b29d` alone was 1078/1101.)
- ⚠ `make test-fast` uses `--timeout=30` but `pytest-timeout` is STILL NOT in `dev.txt`.

## Documentation

- `/CLAUDE.md` (this file) — day-to-day source of truth.
- `/docs/README.md` — index; defers to `/CLAUDE.md`.
- `/docs/architecture.md` — long-form (Version 1.0, **2026-02-06**; STALE).
- `/docs/ui-consistency-audit.md` (refreshed **2026-06-23**) — current; documents the 14-auditor UI-consistency sweep + the applied tokenization/a11y fixes.
- `/docs/deploy/protected-media.md` — prod media authentication strategy (X-Accel-Redirect).
- `/docs/reference/{codebase-inventory,api-surface,frontend-surface,infra-surface}.md` — **STALE** (predate inbox_hub, the access refactor, presence layer, the features, Sprint 0). CLAUDE.md wins on any disagreement.
- **`/docs/qa-audit-2026-06-14/` (11 files)** — the end-to-end QA/security/perf audit that drove the sprint-0 branch. `00-EXECUTIVE-SUMMARY` (38/100 pre-fix), `01`–`06` reports + 4 `_digest_*` appendices. Still-current for the Sprint 1–3 backlog.
- **`/docs/testing/` (relocated 2026-06-30, this pass)** — `manual-testing-checklist.md` (evergreen first-time-user playbook, Phases 0–9) + `qa-run-report-2026-06-29.md` (headless HTTP/API QA run: 38 checks pass, 6 defects — the source of the pass-7 findings: export route 404, no error templates, fresh-tenant 0-seed, KB FTS 500-on-SQLite). Both were untracked at `docs/` root before this pass.
- `/scripts/check_theme.py` + `.theme_baseline.json` — regression guard (baseline **145 / 10 files**; live scan now **137** after the avatar tokenization + §UI/CSS Consistency Pass 2's `verify_email_sent` `#1a1a1a`→token).
- `README.md` — minimal stub.

## Common Pitfalls & Fixes Applied

1. `ACCOUNT_LOGIN_METHODS = {"email"}` (a set); all apps need `migrations/__init__.py`; DRF ≥3.16 (Django 6); `django-celery-beat` removed; `django.contrib.postgres` installed (KB FTS).
2. **`daphne` + `jazzmin` ARE in INSTALLED_APPS** (admin is jazzmin "darkly", superuser-locked; `main/admin.py` registers 0 models).
3. **`main` @ `df9b29d` == `origin/main` (pass 12); the FIVE UI passes + the 2026-07-02 SLA/mobile-triage/triage-lens batch below are now ALL COMMITTED in `df9b29d`. The working tree instead carries the NEW security + go-live batch (17 `M` + 5 `??` — see the pass-12 header at the top).** For historical reference those now-committed passes were: FIVE layered UI passes (avatar tokens + dashboard sizing — §Uncommitted UI Pass; token unification + correctness — §UI/CSS Consistency Pass 2; offcanvas-backdrop fix + landing pricing hidden — §Overlays/Offcanvas Pass; responsive-breakpoint hardening + overlay-block migration — §Responsive/Overlays Pass; **mobile-detection rewrite + page-entrance animations + dashboard carousel/Chart.js fixes — §Responsive Refinement Pass**). The Feature C + Emails/Inbox rename + two behaviour reversals + newsfeed auto-expiry + UI-consistency pass are all COMMITTED (`6c6662d`). **PLUS the 2026-07-02 backend+cockpit batch** — §SLA-Response Fix (first uncommitted backend change), §Mobile Triage Pass, §Triage-Lens Removal + auto-assign inbox handoff. Counts: migrations **122** (120 committed + `inbox_hub/0002` + `inbox_hub/0003` working tree), models **91**, test modules **76** / **1101 collected**, beat 12, tasks 27, NotificationType 21, INTERNAL_ONLY_TYPES **6**, ActivityLog 34, TicketActivity 27, custom-v15.css **25,404** working (25,246 committed), base.html **318** working (292 committed), app.js **1,288** + inbox-hub.js **1,513** (`?v=16`) working/JS **6,006**, theme baseline **145/10** (live **137**), pytest **1120 pass / 22 skip / 1 xfail** (working tree, 1143 collected; committed `df9b29d` was 1078/1101).
4. **Viewer IS seeded — 7 system roles**. **`apps.nav` is NOT installed** (21 `apps.*`).
5. **91 model classes** (Django registry authoritative), 5 polymorphic GenericFK. **Notification is NOT polymorphic.**
6. **`/admin/` NOT in `EXEMPT_PATH_PREFIXES` (17 entries)**. `IsTenantMember` returns True when no tenant. **`/inbound/email/` exempt entry is dead.**
7. **DRF auth order JWT → APIKey → Session.** API keys SHA-512, shown once.
8. **Always use `effective_role`** — BUT raw-`role` drift remains at **10 query sites** (4 user-facing routing/visibility: `agents/services.py:226`, `inbox_hub/assignment.py:246`, `tickets/views.py:1052`/`:1115`; + 6 recipient/actor-selection: `tickets/tasks.py:165`/`:329`, `knowledge/tasks.py:55`, `knowledge/views.py:223`, `crm/tasks.py:279`, `inbound_email/services.py:234`).
9. **⚠ Inbox Hub access is DEPARTMENT-SCOPED** (`inbox_hub/access.py`; UserGroup gate DELETED) — Admin/Manager (≤20) always; Viewer (>30) never; agent-tier iff (in active dept) OR (no active depts, fall-open) OR (has assigned mail). Dept-having tenants must make triagers Department members or they 403.
10. **⚠ Agent ticket visibility — creator KEEPS the ticket after handoff** (`tickets/access.py`, committed `6c6662d`): `Q(assignee=me) | Q(created_by=me)`. **This REVERSES the older "self-created handed-off ticket leaves the creator's view" rule.**
11. **Agent email-inbox handoff** — EVERY assignment path now stamps `InboundEmail.assignee` (manual assign/reassign/claim via `reassign_hub_email` AND engine auto-assign/drain via `assign_to`, §Triage-Lens Removal 2026-07-02). The cockpit has **no assigned-mail lens** — assigned mail is worked from `/inbox/`.
12. **Inbox Hub frontend is a TRIAGE COCKPIT** (`?v=16`, labelled "Emails", assignee chip, **4 lenses** [§Triage-Lens Removal], **mobile chip bar + one-pane swap** [§Mobile Triage Pass]). Backend `claim/escalate/transition/note` exist but UI never calls them. **No `reply` action.**
13. **Inbox Hub presence is heartbeat-driven** — `/ws/live/` 25s ping stamps `last_seen`; `reap_stale_presence` (60s) ages ONLINE→AWAY at 90s TTL; `is_assignable` is the single auto-assign gate.
14. **`first_responded_at` stamped by the RESPONDER's first action** (§SLA-Response Fix, working tree): claim/self-assign (`actor.pk == new_user.pk`), convert, and `transition_hub_email`; manager routing, auto-assign + dismiss deliberately do NOT stamp — routed/auto-assigned-but-untouched mail still breaches (durability layer). The `?sla_risk=true` lens + cockpit badge mirror the sweep (`HubEmail.ACTIVE_SLA_STATES` + `first_responded_at__isnull`). **`escalate_hub_email` is a full no-op without a genuine transition into ESCALATED** (no more count inflation). **Convert↔dismiss cross-transitions rejected** (`assert_transition` → 400). **`PARKED_IN_HUB` IS written** (no longer write-dead).
15. **`apps/inbox_hub/routing.py` is the RoutingEngine**, NOT a Channels route. `apps/inbox_hub/` has NO `signals.py`/`ready()`.
16. **Feature B reachability gap CLOSED** — both surfaces share `ticket_overrides.py::build_ticket_overrides`; full 9-field set reaches both. **Inbound-email attachments** — `attachments.py` + authed `attachment/` action on both viewsets (inline raster / forced download + `nosniff`).
17. **`process_inbound_email` variable name-shadow (readability only)** — local `settings = getattr(tenant,"settings",None)` rebinds the *name* `settings`, but the module has **NO `from django.conf import settings`** (django settings imported locally as `dj_settings`), so it's a misleading name, NOT a collision bug. **IMAP "never backfill"** + ⚠ **IMAP dedup runs `filter(tenant=None,…)` when tenant unresolved** → untenanted shared-mailbox dups with the same Message-ID collapse into one bucket. Filters run BEFORE tenant resolution. **Feature C: confirmation email now sent ONLY if `auto_send_ticket_created_email` is explicitly True (default False).**
18. **BILLING VoIP flags SEEDED** (mig `billing/0003`); re-subscribe repoint. **`require_feature` still 100% dead.** `voip_enabled` gated by `VoIPSettings.is_active`.
19. **VoIP runtime is manual** — `kanzan_voip` queue unconsumed; `run_ari_listener` not in PM2; `cleanup-stale-calls` Beat messages pile up. `CallEventConsumer` tenant-wide (no per-user scoping).
20. **`check_overdue_reminders` + `check_sla_breach_warnings` dead** (not in Beat). **`fire_due_reminders` IS live @30s** (Feature C adds the client-side `ReminderScheduler` for exact-time popups).
21. **Company custom fields synced** (`custom_fields/signals.py` `Company.post_save`). `Account.health_score`/`Contact.lead_score` clamp only in uncalled `clean()`.
22. **Internal comments redacted on the live channel** (`comments/signals.py` → `body: None if is_internal`). **ContactEvent emits ZERO live events.** `TicketPresenceConsumer presence_list` unimplemented.
23. **Kanban drags → ticket service** for cross-status drags (non-personal boards). Pipeline-stage→column sync matches by **name** (rename breaks it).
24. **`Ticket.save()` auto-fills `company`** from linked Contact. **`Article.save()` resolves tenant from context.** `TicketActivity` inner enum is `Event`, NOT `EventType`.
25. **ActivityLog 34 / NotificationType 21 (+REMINDER_DUE) / INTERNAL_ONLY_TYPES 6 (+TICKET_ASSIGNED) / TicketActivity 27.** **HubEmail 9 states / 4 priorities** (no medium; Reminder HAS medium).
26. **`Queue.department` FK opt-in.** **`Board.is_personal`** private. **`Conversation.source_group`** dedupes group convs. **`UserGroup`** "one user per group" (no longer the Hub gate).
27. **No hex literals in CSS/JS/template rule bodies** — `make theme-check` (baseline **145/10**; live scan now **137** after the avatar tokenization + §UI/CSS Consistency Pass 2). Identity avatars are a single theme-reactive red via `--crm-avatar-bg`/`--crm-avatar-fg`. `base.html` toast inline z-index removed (now token-driven). Radius pills unified to `--crm-radius-pill`; line-height scale `--crm-leading-*` added; `--crm-primary-rgb` has a self-contained `:root` fallback.
28. **`tickets/detail.html` Delete-Ticket REMOVED**; macro JS dead no-ops. **`tickets/list.html` stat tabs dynamic.** **Reminders v2 NL quick-add.**
29. **`kb_sidebar_widget.html` orphan** (safe-delete). **`page_back_button.html` included by 18.** **`KBRevision`/`KBTicketLink` dead-everything** (class defs only). **`command-palette.js` "New Contact" → `/contacts/new/`, a SOFT break** (the greedy `contacts/<str:contact_id>/` route renders the detail shell w/ id="new" → HTTP 200 that fails to load; NOT a hard 404; real route `/contacts/create/`). **Landing-page "Start free"/"Get started" CTAs → dead `/signup/` (real route `/register/`).**
30. **CI EXISTS** (`.github/workflows/ci.yml`, PG16+Redis7). **`requirements.txt` byte-identical to `requirements/base.txt`.** **Logs not rotated.** **`make logs-django` errors.** **`make stop`/`restart` skip `kanzan-smtp`.** **`pytest-timeout` missing from `dev.txt`.** **`analytics.DashboardView` under-permissioned.**
31. **Security gaps CLOSED:** DEBUG default→False + `main/checks.py::kanzen.E001` enforcement; messaging tenant-scoping; attachment object-level authz + **authed `/media/`** (`media_views.py`); IMAP per-`(tenant,message_id)` dedup; Stripe replay guard; message-edit fix; HSTS-preload. **`makemigrations --check` clean; 122 migrations** (120 committed incl. `tenants/0011`, + `inbox_hub/0002` [§SLA-Response Fix] + `inbox_hub/0003` [§Triage-Lens Removal backfill] in the working tree).
32. **`window.Toast` now published** (`app.js` end) — previously `const Toast` was module-scoped only, so `window.Toast` feature-detection in other scripts silently fell back to `console.log`/`alert`.
33. **Pass-7 QA-run findings (2026-06-29 headless run → `docs/testing/qa-run-report-2026-06-29.md`):** Settings **Export** button POSTs `/api/v1/analytics/export-jobs/` but the route is `/api/v1/analytics/exports/` → **404**; KB `vote` advertises `{value:±1}` in its OpenAPI schema but the handler reads `{helpful:bool}`; **no custom 404/500/402 templates** (only `403.html`, no `handler404`/`handler500`); a **fresh tenant seeds 0 queues/statuses/categories** (run `setup_queues`+`setup_ticket_statuses`); **XLSX-without-openpyxl → corrupt `.xlsx`** (CSV bytes); `.env` uses the `smtp.gmail.com` backend (real app-password creds, gitignored) so **dev mail bypasses `tmp/emails/`** — swap to `filebased.EmailBackend` to capture locally. Manual-test playbook: `docs/testing/manual-testing-checklist.md`.
