# Frontend Surface — Verified 2026-05-11

> Verified against `main @ bb36325`. Source of truth for **templates, JS modules, CSS, frontend URL routes**.

## Frontend URL routes (`apps/tenants/frontend_urls.py`) — **32 paths**

| Path                                | View                          | Role gate (`_role_required(level)`)         |
|-------------------------------------|-------------------------------|---------------------------------------------|
| `/`                                 | landing_page                  | None                                        |
| `/login/`                           | login_page                    | None                                        |
| `/register/`                        | register_page                 | None                                        |
| `/logout/`                          | logout_page                   | None                                        |
| `/auth/handoff/`                    | auth_handoff                  | None                                        |
| `/verify-email/`                    | verify_email_page             | None                                        |
| `/verify-email-sent/`               | verify_email_sent_page        | None                                        |
| `/setup-company/`                   | setup_company_page            | login_required                              |
| `/workspaces/`                      | workspaces_page               | login_required                              |
| `/dashboard/`                       | dashboard_page                | None                                        |
| `/tickets/`                         | ticket_list_page              | None                                        |
| `/tickets/new/`                     | ticket_create_page            | None                                        |
| `/tickets/<ticket_number>/`         | ticket_detail_page            | None                                        |
| `/contacts/`                        | contact_list_page             | None                                        |
| `/contacts/create/`                 | contact_create_page           | None                                        |
| `/contacts/<contact_id>/`           | contact_detail_page           | None                                        |
| `/calendar/`                        | calendar_page                 | None                                        |
| `/kanban/`                          | kanban_page                   | None                                        |
| `/messaging/`                       | messaging_page                | None                                        |
| `/analytics/`                       | analytics_page                | None                                        |
| `/users/`                           | users_page                    | **20** (Admin/Manager)                      |
| `/settings/`                        | settings_page                 | **20**                                      |
| `/billing/`                         | billing_page                  | **20**                                      |
| `/agents/`                          | agents_page                   | **20**                                      |
| `/emails/`                          | emails_page                   | None                                        |
| `/knowledge/`                       | knowledge_list_page           | None                                        |
| `/knowledge/<article_slug>/`        | knowledge_article_page        | None                                        |
| `/profile/`                         | profile_page                  | None                                        |
| `/inbound-email/`                   | inbound_email_page            | None                                        |
| `/reminders/`                       | reminders_page                | None                                        |
| `/audit-log/`                       | audit_log_page                | **20**                                      |
| `/calls/`                           | calls_page                    | None                                        |

> The previous CLAUDE.md said "28 paths" — actual is 32. Missing from the older count: `/auth/handoff/`, `/workspaces/`, `/inbound-email/`, `/calls/`.

## Templates layout

```
templates/
├── base.html                       (master layout — see below)
├── includes/                       (5 files)
│   ├── navbar.html                 content header (search, theme toggle, agent status)
│   ├── sidebar.html                left nav (Inbox / CRM / Planning / Management groups)
│   ├── softphone.html              VoIP softphone widget (conditional include)
│   ├── messages.html               Django messages display
│   └── kb_sidebar_widget.html      KB sidebar widget
├── pages/                          (31 HTML files across 17 subdirectories)
│   ├── 403.html, calendar.html, dashboard.html, landing.html, login.html, profile.html, register.html
│   ├── agents/list.html
│   ├── analytics/overview.html
│   ├── audit_log/list.html
│   ├── auth/setup_company.html, verify_email_sent.html, verify_email_error.html, workspaces.html
│   ├── billing/plans.html
│   ├── contacts/list.html, create.html, detail.html
│   ├── emails/list.html
│   ├── inbound_email/list.html
│   ├── kanban/board.html
│   ├── knowledge/list.html, article.html
│   ├── messaging/chat.html
│   ├── reminders/list.html
│   ├── settings/tenant.html
│   ├── tickets/list.html, create.html, detail.html
│   ├── users/list.html
│   └── voip/call_history.html
├── auth/email/                     verify_email.{html,txt}
├── tickets/email/                  ticket_created.{html,txt}, reply_notification.{html,txt}, csat_survey.{html,txt}
├── knowledge/email/                article_rejected.{html,txt}
├── notifications/email/            notification.{html,txt}
└── landing/                        landing_crm.html
```

12 email-template files total (6 pairs of `.html` + `.txt`).

## `base.html` (master layout, 205 lines)

Key directives:
- Line 3: `<html lang="en" data-bs-theme="dark">` — **dark mode is the default**
- Lines 12–15: Google Fonts (Inter), Bootstrap 5.3.3, Tabler Icons 3.31.0, Flatpickr CSS — all CDN
- Line 16: `<link href="{% static 'css/custom-v15.css' %}" rel="stylesheet">` — **custom-v15.css is the live CSS**
- **Lines 30–41: per-tenant `primary_color`/`accent_color` override IS ENABLED** (re-enabled in commit `bb36325` on 2026-05-07; previously this block was commented out, the older CLAUDE.md still claimed it was disabled)
- Lines 95–110: Bootstrap JS bundle, DOMPurify 3.2.4, then `api.js`/`app.js`/`command-palette.js`/`custom-select.js`. Conditional (when authenticated): `agent-availability.js`, `notes-panel.js`, `keyboard-shortcuts.js`, `ticket-feed.js`. Conditional VoIP block (`voip_enabled`): include `softphone.html` + load SIP.js 0.21.2 + `voip-softphone.js`.
- Lines 113–197: Flatpickr loader with three CDN fallbacks (jsdelivr → cdnjs → unpkg)
- Line 201: `KanzenSelect.upgradeAll()` to swap native `<select>`s for the custom dropdown

## Static JS (`static/js/`) — 11 modules, 3,213 lines

| File                        | Lines | Purpose                                                                                  |
|-----------------------------|-------|------------------------------------------------------------------------------------------|
| `api.js`                    | 85    | Central API client (CSRF cookie, session creds, JSON+multipart). `get/post/patch/put/delete/upload` |
| `app.js`                    | 522   | Global init: alerts, notification WS, `Toast.{success,error,warning,info}`, cross-page toasts via sessionStorage, date/time formatters, sidebar badge polling, density preference |
| `agent-availability.js`     | 121   | Status toggle + persistence                                                              |
| `theme.js`                  | 77    | light/dark/system theme switcher (default dark) — listens to `prefers-color-scheme`     |
| `command-palette.js`        | 337   | Cmd+K/Ctrl+K modal: 12 static pages + 2 quick actions + dynamic search                   |
| `custom-select.js`          | 371   | `KanzenSelect` portal-rendered dropdown (searchable when >8 options)                     |
| `keyboard-shortcuts.js`     | 318   | Hotkeys: j/k navigate · Enter open · Esc deselect · a/s/x row actions · Ctrl+K palette · c new · ? help · g d/t/c/b go-to. Disabled inside inputs. |
| `notes-panel.js`            | 238   | Quick notes CRUD (6 colors, pinning, localStorage)                                       |
| `rich-editor.js`            | 191   | TipTap wrapper for comments and KB articles                                              |
| `ticket-feed.js`            | 201   | WS `ws/tickets/feed/` — toasts + new-tickets banner + row pulse, exponential reconnect (max 10, 30s cap) |
| `voip-softphone.js`         | 710   | SIP.js 0.21.2 softphone + `CallEventConsumer` integration; dial pad, DTMF, mute/hold/transfer/hangup, incoming-call modal |

## Static CSS (`static/css/`)

| File              | Lines  | Status                                                  |
|-------------------|--------|---------------------------------------------------------|
| `custom-v15.css`  | 21,208 | **Live** — referenced by `base.html`. Uncommitted (working file). |
| `custom.css`      | 20,431 | Committed copy (no longer loaded; older red-theme baseline).      |

Design system **"Crimson Black v9"** — primary `#C1121F`, accent `#E11D2D`, dark-mode-first. ~160 CSS custom properties under `:root` + `[data-bs-theme="dark"]`: `--crm-primary*` (50–900), `--crm-bg-*`, `--crm-text-*`, `--crm-sidebar-*`, `--crm-status-*`, `--crm-priority-*`, `--crm-shadow-*`, `--crm-duration-*`, `--crm-ease*`, `--crm-radius-*`. Sidebar 252px (white in light mode, near-black in dark) with red accent bar on the active item.

## External CDN dependencies (loaded by base.html)

| Asset                | Version | Source                                                   |
|----------------------|---------|----------------------------------------------------------|
| Google Fonts (Inter) | latest  | fonts.googleapis.com                                     |
| Bootstrap            | 5.3.3   | cdn.jsdelivr.net                                         |
| Tabler Icons         | 3.31.0  | cdn.jsdelivr.net                                         |
| Flatpickr            | latest  | jsdelivr (with cdnjs + unpkg fallbacks)                  |
| DOMPurify            | 3.2.4   | cdn.jsdelivr.net                                         |
| SIP.js               | 0.21.2  | cdn.jsdelivr.net (conditional — only when `voip_enabled`) |

TipTap is loaded inside `rich-editor.js` (not from base.html).

## Tenant context processor (`apps/tenants/context_processors.py`)

Injects 9 keys into every template:
1. `tenant`
2. `membership`
3. `user_role` — uses `membership.effective_role` (respects temporary-role overrides)
4. `is_admin` (hierarchy_level ≤ 10)
5. `is_admin_or_manager` (≤ 20)
6. `is_agent_or_above` (≤ 30)
7. `voip_enabled` — checks `VoIPSettings.is_active` for current tenant
8. `BASE_URL`

## Sidebar groups (`templates/includes/sidebar.html`)

- **Inbox:** Tickets · Emails · Messages · Calls (currently commented out)
- **CRM:** Contacts · Boards · Knowledge Base
- **Planning:** Calendar · Reminders
- **Management** (gated by `is_admin_or_manager`): Analytics · Agents/Users (commented out) · Audit Log
- **User footer:** Profile · Settings · Sign Out

Badge counters polled from `/api/v1/nav/badge-counts/` every 60s. Cap at 99 ("99+" rendered above).
