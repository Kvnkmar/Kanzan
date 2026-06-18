# Kanzen — QA Report
**Date:** 2026-06-14 · **HEAD:** `9575577` + 2 uncommitted features · **Verdict:** Not production-ready (38/100)

## 1. Methodology & limitations

This was a **static + test-execution** audit (no live browser/E2E run, because the app requires a running multi-process stack — ASGI + Celery + Redis + Channels + Asterisk). Coverage was achieved via:

1. **Quality gates** run live: `pytest` (full suite), `ruff`, `makemigrations --check`, `scripts/check_theme.py`.
2. **26 parallel read-only audit streams** (one per subsystem × dimension), each grounding findings in actual source with `file:line` citations.
3. **Adversarial verification** — every Critical/High finding was independently re-checked by a second agent instructed to *refute* it; 3 Critical + 15 High claims were rejected as false positives.
4. **Human adjudication** — the 6 top Criticals were hand-verified against source by the lead auditor.

**What was NOT covered** (recommend follow-up): live cross-browser/responsive testing, real Stripe/Asterisk/IMAP integration runs, load/soak testing, and visual regression. Findings about runtime UI behavior are inferred from template/JS code.

## 2. Quality-gate results

| Gate | Result | Detail |
|---|---|---|
| `makemigrations --check --dry-run` | ✅ Clean (exit 0) | 118 migrations, 2 untracked describe the working-tree deltas |
| `scripts/check_theme.py` | ✅ Pass | 146 hex literals tracked vs baseline; both new features add zero hex |
| `ruff check .` | ⚠️ **196 issues** | 146 unused-import, 36 unused-var, 9 f-string-no-placeholder, 3 ambiguous-name, **1 repeated dict key**, 1 redefinition |
| `pytest` (full) | ❌ **18 failed / 834 passed / 22 skipped / 1 xfailed** | 169 s |

### 2.1 Ruff issues worth attention (not pure style)
- **`F601` repeated dict key** `"mark_all_read"` in `apps/accounts/permissions.py:93` (`ACTION_MAP`) — harmless today (both map to `view`) but a latent permission-mapping hazard if one is edited.
- **`F841` assigned-never-used** in *production* code: `apps/inbound_email/api_views.py:400`, `apps/tickets/views.py:613`, `apps/tickets/signals.py:790`, `apps/notifications/views.py:209`, `apps/messaging/mentions.py:198`, `apps/voip/signals.py:153` — each should be reviewed for dropped logic.
- **`F811`** redefinition in `tests/test_ticket_presence.py:34`.

## 3. Test-suite analysis

**The documented "green except 2 badge tests" status is false.** The full suite has **18 failures**. Root-caused in isolation:

| Group | Count | Real bug? | Root cause |
|---|---|---|---|
| `test_outbound_email.py` + `test_email_outbound.py` | 15 | **No (test debt)** | Outbound hardened to skip RFC 2606 domains (`example.com`, `*.test`, `*.local`) via `notifications/utils.py::is_undeliverable_email`; tests still send to those domains → `mail.outbox` empty → `0 != 1` |
| `test_badges.py` (14.05, 14.09) | 2 | No (stale) | Comments→chat badge repurpose; tests never updated |
| `test_comment_visibility.py::test_viewer_cannot_see_internal_comments` | 1 | No (stale, fails safe) | Tightened ticket-access now returns 404 for Viewer at the comments endpoint; test expected 200 |

**QA implications (these ARE findings):**
- **`QA-1` (Medium):** The outbound-email regression suite is entirely red and protects nothing. Either update fixtures to a deliverable domain or add an `@override_settings`-driven allowlist for tests.
- **`QA-2` (Medium):** No CI exists; `make check` is the only gate and is currently red. A red local gate means regressions ship unnoticed (the email behavior change went undetected).
- **`QA-3` (Medium):** Coverage gaps on critical paths — the Feature B `create_ticket` action + `_build_ticket_overrides` validator, `DashboardView` authz, billing webhooks, WebSocket auth, and attachment MIME/authz have **no direct tests**.
- **`QA-4` (Low):** `make test-fast` passes `--timeout=30` but `pytest-timeout` is absent from `requirements/dev.txt`; `pytest.ini` sets no `asyncio_mode`.

## 4. Functional coverage matrix (by subsystem)

Health = adjudicated state after verification. C/H/M = confirmed Critical/High/Medium counts attributed.

| Subsystem | Health | C | H | Headline issue |
|---|---|---|---|---|
| Multi-tenancy / isolation | ⚠️ | 1 | — | `InboundEmail` not tenant-scoped; cross-tenant IMAP dedup |
| AuthN (JWT/APIKey/session/SSO) | 🟢 | — | 1 | Invitation token reuse on race; otherwise solid |
| AuthZ / RBAC | ⚠️ | — | 3 | `role` vs `effective_role` drift (20+ sites) |
| Inbox Hub access & engine | ⚠️ | — | 4 | SLA `first_responded_at` never written → always breaches/escalates |
| Tickets CRUD / serializers | ⚠️ | — | 3 | cross-tenant `assignee`; `clean()` never called |
| Tickets services / SLA | ⚠️ | — | 1 | merge/split lock released after `.exists()` |
| Tickets signals / dual-write | ⚠️ | — | 1 | pipeline→kanban sync by NAME breaks on rename |
| Inbound email pipeline | ⚠️ | (1) | — | (shared w/ tenant-isolation) |
| Feature A — reminder-due | 🟢 | — | 2 | backward-reschedule race; recipient-deleted race |
| Feature B — ticket overrides | 🟢 | — | 1 | Hub convert serializer not widened (reachability gap) |
| Notifications | 🟢 | — | — | Sound; internal-only enforcement OK |
| WebSockets / Channels | ⚠️ | — | 2 | VoIP consumer tenant-wide leak; presence no visibility check |
| Contacts / CRM | 🟢 | — | 1 | contact-context cache never invalidated |
| Knowledge base | ⚠️ | — | 1 | FTS broken on SQLite (dev); no documented fallback |
| Kanban | ⚠️ | — | (1) | (shared: column rename) |
| Messaging | ⚠️ | 1 | 1 | cross-tenant user injection; blank body persists |
| Billing / Stripe | 🔴 | 2 | — | VoIP dead; re-subscribe IntegrityError |
| VoIP | ⚠️ | — | 1 | tenant-wide call metadata broadcast |
| Analytics / exports | ⚠️ | — | 1 | `DashboardView` RBAC bypass; export format mismatches |
| Attachments | 🔴 | 1 | 2 | object-level authz absent on upload/retrieve/download |
| Custom fields / presence | ⚠️ | — | 3 | Company never synced; `visible_to_roles` unenforced; tz bug |
| Settings / secrets / infra | 🔴 | 1 | — | DEBUG split-default footgun |
| Frontend JS | ⚠️ | — | 2 | WS backoff inconsistency; reachability gap; dead link (M) |
| Templates / UI-UX | ⚠️ | — | 1 | a11y: password toggle not keyboard-accessible |
| Performance / DB | ⚠️ | — | 1 | partial-index/task mismatch; N+1s |
| Data-model / migrations | ⚠️ | — | 2 | assignee cross-tenant; NewsPost CASCADE data loss (M) |

## 5. CRUD / forms / navigation spot-observations
- **CRUD:** Create/Read/Update/Delete work across the major resources (tickets, contacts, KB, kanban, etc.). The integrity gaps are in *validation* (cross-tenant FKs, `clean()` skipped), not in basic CRUD wiring.
- **Forms / validation:** Feature B's `_build_ticket_overrides` correctly returns **400, not 500**, on bad input (a good pattern). Messaging allows **blank message bodies** (`messaging-4`). Model-level `clean()` validators are broadly **not invoked** by `save()`/serializers.
- **Navigation / links:** Command-palette "New Contact" → `/contacts/new/` is a **dead link** (real route `/contacts/create/`). Sidebar Inbox-Hub entry is correctly group-gated. `kb_sidebar_widget.html` is an orphan template.
- **Buttons/menus:** Inbox Hub cockpit never calls the live `claim/escalate/transition/note` endpoints (dead-but-present API surface). `tickets/detail.html` carries ~44 lines of dead macro JS.

## 6. Recommendation
Treat the **6 Criticals + the role/`effective_role` drift + attachment authz** as launch blockers. Re-green the test suite and stand up CI before any production deploy. See `06-REMEDIATION-PLAN.md`.
