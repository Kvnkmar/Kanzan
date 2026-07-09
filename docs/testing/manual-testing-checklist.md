# Kanzen — First-Time-User Testing Checklist (condensed)

> Tick each item. Full detail/expected-results in `manual-testing-guide.md`. Test top-to-bottom.

## Phase 0 — Environment
- [ ] App on port 8001, Redis up, Celery worker + beat running
- [ ] `python manage.py run_smtp_server` (port 2525) for inbound email
- [ ] `python manage.py seed_plans`
- [ ] Know: auth = bare domain `localhost:8001`; app = `<slug>.localhost:8001`; dev emails → `tmp/emails/`

## Phase 1 — Onboarding
- [ ] Landing `/` renders; Login link works; sign-up CTAs 404 (dead `/signup/`)
- [ ] Register `/register/` → inactive account + redirect to verify-email-sent
- [ ] Register negatives: mismatched pw, duplicate email, weak pw
- [ ] Get verify link from `tmp/emails/`, open `/verify-email/?token=` → activated + logged in
- [ ] Verify-email error states: missing / tampered / used / expired token
- [ ] Setup company `/setup-company/` → tenant created, you're Admin, land on dashboard
- [ ] Setup negatives: reserved slug, taken slug, invalid slug
- [ ] Workspaces picker `/workspaces/` (with 2+ tenants)
- [ ] Logout `/logout/` → kills all your sessions
- [ ] Profile `/profile/`: inline-edit fields, email locked, avatar upload

## Phase 2 — Workspace shell
- [ ] Dashboard `/dashboard/`: cards, trends, doughnut, urgent panel, recent activity
- [ ] Summary cards deep-link to filtered tickets; date pills refetch
- [ ] Admin/Manager-only: Agent Status, Top Agents, SLA Compliance
- [ ] Real-time: 2nd tab creates/resolves ticket → dashboard updates without reload
- [ ] Cmd+K palette (note: "New Contact" dead-links to `/contacts/new/`)
- [ ] `?` shortcuts overlay; `g d/t/c/b` navigation
- [ ] Navbar "+" Quick Create auto-opens kanban/calendar modals

## Phase 3 — Tickets
- [ ] List `/tickets/`: stat tabs, filters, search, Table/Card toggle persists
- [ ] Bulk select → Assign / Status / Priority / Delete (only delete path)
- [ ] Create `/tickets/new/`: Subject + Queue (labeled "Subcategory") required
- [ ] Inline "+" creates a contact; attachments ≤25MB; tags
- [ ] Detail `/tickets/<n>/`: inline subject/description edit
- [ ] Sidebar selects auto-save (no Save button)
- [ ] Public comment vs Internal note (lock checkbox)
- [ ] SLA status section; resolve → CSAT email scheduled
- [ ] Set Reminder works (no Macros button / no Delete / Watchers API-only)
- [ ] Kanban `/kanban/`: team board drag changes status; personal board doesn't

## Phase 4 — CRM
- [ ] Contacts `/contacts/`: search, detail panel, bulk delete, saved views
- [ ] Create `/contacts/create/`: First/Last/Email required; inline company
- [ ] Create negatives: duplicate email, plan cap (photo is preview-only/not saved)
- [ ] Edit `/contacts/<id>/`: save/delete
- [ ] Companies/Accounts/Contact-Groups: API-only (verify via `/api/docs/`)
- [ ] Reminders `/reminders/`: NL quick-add, complete, reschedule, bulk
- [ ] **Exact-time popup:** reminder assigned to self ~2min out, tab open, click once → modal+chime+notification at due time
- [ ] Calendar `/calendar/`: tickets/reminders/events, add event, filters persist

## Phase 5 — Email & Inbox Hub
- [ ] Settings: enable `inbox_hub_enabled` (OFF by default)
- [ ] CLI: `seed_inbox_hub_defaults --tenant-slug <slug>`
- [ ] Send test email to `localhost:2525` → appears in `/inbound-email/` log
- [ ] Inbound negatives: unresolvable address → 550; thread replies skip hub
- [ ] Cockpit `/emails/`: lenses, click row, Convert (C) / Assign (A) / Dismiss (X)
- [ ] Personal Inbox `/inbox/`: assigned mail, create-ticket, link, reply
- [ ] Convert with overrides → verify priority/queue/status/assignee on ticket
- [ ] Convert negatives: invalid priority, closed status, non-member assignee, idempotent
- [ ] Attachments: images inline, others forced download

## Phase 6 — Knowledge & Collaboration
- [ ] KB `/knowledge/`: browse, in-page search, open article (view count++)
- [ ] KB workflow (2 accounts): agent draft → submit → admin approve/reject
- [ ] Note: FTS endpoint `/api/v1/knowledge/search/?q=` 500s on SQLite
- [ ] Messaging `/messaging/`: DM + group create
- [ ] **Real-time (2 browsers):** message appears live; typing indicator
- [ ] Messaging attachments live
- [ ] Newsfeed (dashboard card): post, draft-vs-publish, react, read receipt, 24h auto-expiry
- [ ] Quick Notes: add/color/pin/delete; per-user isolation
- [ ] Notifications bell: trigger one, mark read, mark-all-read, live update

## Phase 7 — Admin & Settings (navigate by direct URL — many sidebar links hidden)
- [ ] Users `/users/`: invite (resend/delete), add user, negatives
- [ ] Roles: API-only; system role delete → 403
- [ ] Groups `/groups/`: create; one-user-per-group conflict disables Save
- [ ] Agents `/agents/`: presence, change role, grant/revoke temp role, deactivate
- [ ] Agents negatives: temp-role to self blocked, Admin temp blocked, Manager has no Actions
- [ ] Temp-role drift: promote Agent→Manager, check routing still uses raw role
- [ ] Settings `/settings/`: loads for all, writes admin-only (Agent PATCH → 403)
- [ ] Branding: change color → whole app re-themes; invalid hex error
- [ ] Business hours + holidays + general (rename, date format)
- [ ] Billing `/billing/`: upgrade flow; Stripe-unconfigured warning; Manager view-only/403
- [ ] API Keys: generate → reveal-once → regenerate/revoke/delete; Manager 403
- [ ] Custom Fields (API): create def → renders on record; clear custom_data orphan bug
- [ ] Audit Log `/audit-log/`: filters, export CSV/JSON, live dot; Agent → 403
- [ ] Django admin `/admin/`: superuser only; tenant Admin denied
- [ ] **RBAC matrix:** log in as each role (Admin/Manager/TeamLead/Agent/Viewer), confirm sidebar + 403s + write outcomes

## Phase 8 — VoIP & Analytics
- [ ] Calls `/calls/` (direct URL only): empty in dev; note no per-user scoping
- [ ] Softphone: set `VoIPSettings.is_active=True` → widget appears; verify registration attempt
- [ ] Softphone off: widget not rendered
- [ ] Analytics `/analytics/`: KPIs, status/priority bars; Manager-only sections
- [ ] Export (Settings → Data): note button 404s (`export-jobs` vs `exports`); no download UI

## Phase 9 — Cross-cutting (every page)
- [ ] Responsive <992px: sidebar hamburger; dense pages reflow
- [ ] Sidebar collapse persists across refresh (no FOUC)
- [ ] Dark vs light theme: legibility, crimson carets, light auth panels, red avatars
- [ ] Real-time (2 browsers): live updates + badge increments; offline → reconnect pill
- [ ] Reminder-due popup fires globally
- [ ] Multi-tenant isolation: tenant A can't reach tenant B records; unknown subdomain → 404
- [ ] Empty states on every list (not blank/spinner)
- [ ] Loading states + error toasts on slow/failed API
- [ ] Error pages: 403 branded; probe 404 / 402 / 500 (no custom templates — flag it)

## Confirmed defects (file once — don't re-report)
- [ ] Landing sign-up CTAs → dead `/signup/`
- [ ] Cmd+K "New Contact" → dead `/contacts/new/`
- [ ] Export button → 404 (`export-jobs` vs `exports`); no download UI
- [ ] KB FTS search 500 on SQLite
- [ ] No custom 404/402/500 templates
- [ ] KB vote schema mismatch (`{value:1}` vs `{helpful:bool}`)
- [ ] Custom-data clear → orphaned CustomFieldValue rows
- [ ] CallEventConsumer/CallLog tenant-wide (no per-user scoping)
