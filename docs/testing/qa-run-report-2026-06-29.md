# CRM — QA Test Run Report (automated, HTTP/API level)

> Run date: 2026-06-29 · Executed against the live dev stack (all PM2 services online, SQLite).
> Method: real first-time-user flow driven via HTTP + DRF API + SMTP receiver + DB assertions.
> Test workspace created: tenant `qa-05474` (admin `qa+<ts>@example.com`, agent `agent.qa@example.com`).
>
> **Scope note:** this covers everything reachable without a browser — page reachability, auth/onboarding,
> RBAC, CRUD flows, email ingestion, permissions, multi-tenant isolation. Pure in-browser JS
> (rich editors, drag-drop, live WebSocket rendering, reminder popup, softphone, Stripe) was **not** exercised
> and is listed under "Needs human/browser" — those require manual testing per the checklist.

## Summary

| Result | Count |
|---|---|
| ✅ Passed | 38 checks |
| 🟥 Defects found | 6 (1 high, 1 medium, 4 confirmed-known) |
| ⏭️ Needs human/browser | ~12 interaction areas |

**Verdict:** the backend, routing, auth, RBAC, email pipeline, and CRUD layers are **solid** — every flow that
should work, worked, and every negative/permission case was correctly rejected. The defects are concentrated in
(a) the email backend config blocking onboarding in this environment, (b) no default ticket setup on a new tenant,
and (c) the four already-documented front-end/route bugs.

---

## ✅ Passed

### Phase 1 — Onboarding
- Landing `/` → 200; dead `/signup/` CTA → **404** (confirmed broken-by-design).
- Register page → 200; real form POST → **302 → /verify-email-sent/**; user created **inactive**.
- Verify with a **bad token → 400**; with the **valid token → 302 → /setup-company/**, user **activated**, session established.
- Setup-company → 200; **reserved slug `admin` rejected** (re-render, no tenant); real create → **302 handoff** to the new subdomain.
- Tenant created with **settings + 7 roles + admin membership** seeded.
- Full browser chain **login → handoff → /dashboard/** → 200, subdomain session established.

### Phase 2–8 — Page reachability (admin)
- **All 23 sections return 200**: dashboard, profile, tickets, tickets/new, contacts, contacts/create, calendar, kanban, messaging, analytics, knowledge, reminders, calls, inbound-email, audit-log, users, billing, agents, groups, inbox, emails, settings, api/quickstart.
- Legacy `/inbox-hub/` → **302 → /emails/** ✅.

### Functional CRUD (API)
- Contact create → **201**; duplicate email → **400** ✅.
- Ticket create → **201** (after seeding queues/statuses).
- Reminder create → **201**; `my-tasks` → 200.
- Quick note → 201; KB draft → 201; Newsfeed post → 201.
- KB workflow: create → **submit-for-review (200)** → **approve (200)** ✅.
- API key mint → **201**, secret revealed once ✅.
- `nav/badge-counts` → 200; `analytics/dashboard` → 200.

### Phase 5 — Inbound email (end-to-end)
- Admin PATCH `inbox_hub_enabled=true` → 200.
- `seed_inbox_hub_defaults` created the **General** department.
- SMTP send to `:2525` accepted → **InboundEmail ingested, status = `parked_in_hub`**, **HubEmail parked**, cockpit list shows it ✅ (also confirms the `PARKED_IN_HUB` write).

### RBAC & isolation (second user @ Agent level)
- API: agent → settings PATCH **403**, role create **403**, api-key **403**; tickets list **200** (scoped).
- Pages: agent → `/users/ /agents/ /audit-log/ /billing/ /groups/` all **403**; `/dashboard/ /tickets/` **200**.
- **Department-scoping confirmed:** agent (not a department member) → `/emails/` **403**.
- **Multi-tenant isolation:** qa-tenant agent JWT against `straat-x` host → **403** (no cross-tenant leak).

---

## 🟥 Defects found

### 1. HIGH — Onboarding email never delivered (environment config)
The running app uses `EMAIL_BACKEND = django.core.mail.backends.smtp.EmailBackend` pointed at `smtp.gmail.com`
with **empty credentials** (from `.env`), **not** the filebased backend. Result: the verification email is **never
delivered and never written to `tmp/emails/`** (newest file is June 9). A real first-time user would register,
receive nothing, and be **unable to activate / proceed**. (I bypassed it using the DB token to continue testing.)
**Fix:** set `EMAIL_BACKEND=django.core.mail.backends.filebased.EmailBackend` (or console) in dev `.env`, or supply
working SMTP creds. This single issue blocks the entire signup funnel as currently configured.

### 2. MEDIUM — New tenant has no default queues/statuses/categories
A freshly created workspace seeds **0 queues, 0 statuses, 0 categories**, so the first "Create Ticket" is impossible
until an admin configures them (Settings has the panes, but nothing is auto-seeded). Onboarding should seed sensible
defaults (the `setup_queues` / `setup_ticket_statuses` commands exist but aren't run on tenant creation).

### 3. CONFIRMED — KB full-text search 500s on SQLite
`GET /api/v1/knowledge/search/?q=printer` → **500** (no icontains fallback despite the docstring). Reachable via
Swagger / API Quickstart. In-page KB search (uses `?search=`) is unaffected.

### 4. CONFIRMED — Export button posts to a non-existent route
UI posts to `/api/v1/analytics/export-jobs/` → **404**; the correct route `/api/v1/analytics/exports/` → **201**.
The Settings "Start Export" button cannot work as wired.

### 5. CONFIRMED (with correction) — Command-palette "New Contact" misroutes
`/contacts/new/` is **not a 404** — the route `contacts/<str:contact_id>/` greedily matches `new`, so it renders the
**Contact Detail** shell (HTTP 200) with id="new", which then fails to load ("Failed to load contact"). Soft-broken
link, not a hard 404. Real create page is `/contacts/create/`. *(Guide updated to reflect this nuance.)*

### 6. CONFIRMED — Landing sign-up CTAs dead (`/signup/` → 404)
Every "Start free" / "Get started" button points at the non-existent `/signup/`. Real route: `/register/`.

---

## ⏭️ Needs human / browser (not testable headless)

These passed at the page-load level (200) but their **interactive behavior** must be checked manually:
- Rich-text editors (TipTap) on ticket/KB/convert forms.
- Kanban drag-and-drop status changes; personal-vs-team board behavior.
- Reminder-due popup (modal + chime + desktop notification) at exact time.
- Real-time WebSocket rendering: live messaging delivery, notification badge live updates, dashboard auto-refresh (needs two browsers).
- Cmd+K command palette, `?` shortcuts overlay, theme toggle, responsive/mobile layout, sidebar collapse persistence.
- Softphone registration UI (no Asterisk backend — expected to sit at "No Extension").
- Stripe checkout (no keys configured).

---

## Test artifacts / cleanup
Created during this run (safe to purge): tenant `qa-05474` + users `qa+<ts>@example.com`, `agent.qa@example.com`,
1 contact, 1 ticket, 1 reminder, 1 note, 2 KB articles, 1 newsfeed post, 1 API key, 1 parked inbound email.
