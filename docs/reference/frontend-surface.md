# Frontend Surface — Verified 2026-05-22

> Verified against `main @ ea87bb2` (code state at `241e407`). Source of truth for **templates, JS modules, CSS, frontend URL routes**. Pairs with `/CLAUDE.md`.

## Frontend URL routes (`apps/tenants/frontend_urls.py`) — **34 paths**

| Path                                | View                          | Role gate                                              |
|-------------------------------------|-------------------------------|--------------------------------------------------------|
| `/`                                 | landing_page                  | None                                                   |
| `/login/`                           | login_page                    | None                                                   |
| `/register/`                        | register_page                 | None                                                   |
| `/logout/`                          | logout_page                   | None                                                   |
| `/auth/handoff/`                    | auth_handoff                  | None (must resolve tenant — NOT in middleware exempt list) |
| `/verify-email/`                    | verify_email_page             | None                                                   |
| `/verify-email-sent/`               | verify_email_sent_page        | None                                                   |
| `/setup-company/`                   | setup_company_page            | `login_required`                                       |
| `/workspaces/`                      | workspaces_page               | `login_required`                                       |
| `/dashboard/`                       | dashboard_page                | `_membership_required`                                 |
| `/tickets/`                         | ticket_list_page              | `_membership_required`                                 |
| `/tickets/new/`                     | ticket_create_page            | `_membership_required`                                 |
| `/tickets/<ticket_number>/`         | ticket_detail_page            | `_membership_required`                                 |
| `/contacts/`                        | contact_list_page             | `_membership_required`                                 |
| `/contacts/create/`                 | contact_create_page           | `_membership_required`                                 |
| `/contacts/<contact_id>/`           | contact_detail_page           | `_membership_required`                                 |
| `/calendar/`                        | calendar_page                 | `_membership_required`                                 |
| `/kanban/`                          | kanban_page                   | `_membership_required`                                 |
| `/messaging/`                       | messaging_page                | `_membership_required`                                 |
| `/analytics/`                       | analytics_page                | `_membership_required`                                 |
| `/users/`                           | users_page                    | **`_role_required(20)`**                               |
| `/settings/`                        | settings_page                 | `_membership_required` + `@ensure_csrf_cookie` (API enforces admin write) |
| `/billing/`                         | billing_page                  | **`_role_required(20)`**                               |
| `/agents/`                          | agents_page                   | **`_role_required(20)`**                               |
| `/groups/`                          | groups_page                   | **`_role_required(20)`** ← UserGroup management        |
| `/emails/`                          | emails_page                   | **`_role_required(30)`** ← Outbound log; admits Team Lead/Agent/IT/HR |
| `/knowledge/`                       | knowledge_list_page           | `_membership_required`                                 |
| `/knowledge/<article_slug>/`        | knowledge_article_page        | `_membership_required`                                 |
| `/profile/`                         | profile_page                  | `_membership_required`                                 |
| **`/api/quickstart/`**              | **api_quickstart_page**       | `_membership_required` ← Developer guide for API keys  |
| `/inbound-email/`                   | inbound_email_page            | `_membership_required` (agent inbox)                   |
| `/reminders/`                       | reminders_page                | `_membership_required`                                 |
| `/audit-log/`                       | audit_log_page                | **`_role_required(20)`**                               |
| `/calls/`                           | calls_page                    | `_membership_required` (VoIP call history)             |

## Templates layout (47 `.html` files total)

```
templates/
├── base.html                       (master layout, 265 lines)
├── includes/                       (6 files)
│   ├── navbar.html                 search, theme toggle, agent status, #liveStatusPill, #notifFlyout (bell-anchored card)
│   ├── sidebar.html                left nav with hidden items (Emails/Calls/Agents/Users/Settings/Billing commented), footer dropdown with /api/quickstart/
│   ├── softphone.html              VoIP softphone widget (conditional include on voip_enabled)
│   ├── messages.html               Django messages display
│   ├── kb_sidebar_widget.html      KB sidebar widget (ORPHAN — no template includes it)
│   └── page_back_button.html       NEW — #pageBackBtn; hidden on sidebar pages, shows on non-sidebar pages. Wired into 17 pages.
├── pages/                          (17 subdirectories + 8 root files)
│   ├── 403.html, api_quickstart.html, calendar.html, dashboard.html, landing.html, login.html, profile.html, register.html
│   ├── agents/list.html
│   ├── analytics/overview.html
│   ├── audit_log/list.html         "Insights" redesign — heat ribbon, chip rail, drawer (~1,265 lines)
│   ├── auth/setup_company.html, verify_email_sent.html, verify_email_error.html, workspaces.html
│   ├── billing/plans.html
│   ├── contacts/list.html, create.html, detail.html
│   ├── emails/list.html
│   ├── groups/list.html
│   ├── inbound_email/list.html
│   ├── kanban/board.html           Calendar-ported filter panel, SortableJS DnD, 1,380-line script block
│   ├── knowledge/list.html, article.html
│   ├── messaging/chat.html         Pending-attachments tray + buildAttachmentBlock helper
│   ├── reminders/list.html
│   ├── settings/tenant.html        Searchable settings hub (5,086 lines, ~20 panes incl. apiKeysPane/apiDocsPane)
│   ├── tickets/list.html, create.html, detail.html
│   ├── users/list.html
│   └── voip/call_history.html
├── auth/email/                     verify_email.{html,txt}
├── tickets/email/                  ticket_created.{html,txt}, reply_notification.{html,txt}, csat_survey.{html,txt}
├── knowledge/email/                article_rejected.{html,txt}
├── notifications/email/            notification.{html,txt}
└── landing/                        landing_crm.html (1,393 LOC — standalone, doesn't extend base.html)
```

12 email-template files total (6 pairs of `.html` + `.txt`).

### `page_back_button.html` — 17-page wire-up

Renders `<button#pageBackBtn>` hidden by default; only shows on non-sidebar pages (checks `window.location.pathname` against a **hardcoded list of sidebar paths**: `['/dashboard/','/tickets/','/messaging/','/contacts/','/kanban/','/knowledge/','/calendar/','/reminders/','/analytics/','/audit-log/']`). Click → `history.back()` when `document.referrer` is same-origin, else `/dashboard/`.

Included by: `api_quickstart`, `audit_log/list`, `groups/list`, `kanban/board`, `agents/list`, `profile`, `reminders/list`, `tickets/list`, `billing/plans`, `analytics/overview`, `voip/call_history`, `users/list`, `calendar`, `inbound_email/list`, `knowledge/list`, `emails/list`, `contacts/list`.

> ⚠️ **When adding a new top-level sidebar page, update the hardcoded sidebar-paths array** in `templates/includes/page_back_button.html` — otherwise the Back button will incorrectly render on the new page.

## `base.html` (master layout, 265 lines)

Key directives:
- Line 3: `<html lang="en" data-bs-theme="dark">` — **dark mode is the default**
- Google Fonts (Inter), Bootstrap 5.3.3, Tabler Icons 3.31.0, Flatpickr CSS — all CDN
- `<link href="{% static 'css/custom-v15.css' %}" rel="stylesheet">` — **custom-v15.css is the live CSS**
- **Lines 30–86: per-tenant `primary_color`/`accent_color` override IS ENABLED** — emits ~35 CSS variables (palette + Bootstrap overrides + semantic-red retheme + focus glows + `--crm-gradient`). Selector `:root, [data-bs-theme="light"], [data-bs-theme="dark"]` so it wins over `custom-v15.css` defaults.
- **Synchronous `crm_sidebar_collapsed` localStorage check at body open** to apply `.sidebar-collapsed` pre-paint (FOUC fix)
- Mobile detection IIFE adds `is-mobile-html`/`is-mobile`/`is-mobile-sm` body classes
- Script load order: Bootstrap → DOMPurify → **`live-bus.js`** (always) → `api.js` → `app.js` → `command-palette.js` → `custom-select.js` → **conditional on `tenant and user.is_authenticated`**: **`live-connection.js`** → `agent-availability.js` → `notes-panel.js` → `keyboard-shortcuts.js` → `ticket-feed.js` → (if `voip_enabled`) SIP.js CDN + `voip-softphone.js`.
- Flatpickr loader with three CDN fallbacks (jsdelivr → cdnjs → unpkg)
- `CRMSelect.upgradeAll()` to swap native `<select>`s for the custom dropdown

## Static JS (`static/js/`) — 13 modules, ~4,031 LOC

| File                        | LOC  | Purpose                                                                                  |
|-----------------------------|-----:|------------------------------------------------------------------------------------------|
| **`live-bus.js`**           | 175  | Global pub/sub `window.LiveBus`. API: `on/onMany/publish/debounce/rafBatch/isConnected/setChannelState`. Wildcard `"*"` subscriber receives every event. Cross-tab fan-out via optional `BroadcastChannel('crm-live')`. |
| **`live-connection.js`**    | 206  | Single shared `wss?:/ws/live/`. 25s heartbeat / 8s pong timeout. Exponential backoff 1s→30s + ±20% jitter, **infinite retries**. Tab-visibility hook for instant reconnect. Publishes `live.reconnected` after recovery. |
| `api.js`                    | 90   | Central API client (CSRF cookie + meta fallback, session creds, JSON+multipart)          |
| `app.js`                    | 843  | Global init: alerts, sidebar collapse, density, notification WS, Toast (uses `var()` colours), `CRM.formatDate/formatDateTime/timeAgo`, sidebar badge polling, `initLiveStatusPill()`, `initSidebarUserLive()`, `initSidebarBadges()`. New-notification WS handler calls `ringBell()` (~950ms swing + radial halo) + `showFlyout(data)` displays bell-anchored peek-preview for **3s** with progress bar; `updateBadge(count, {bump:true})` triggers `.is-bumping` scale animation. |
| `ticket-feed.js`            | 247  | WebSocket `ws/tickets/feed/`. Auto-connects via `data-ticket-feed` or URL match. Banner + row pulse. Publishes into LiveBus (`ticket.<verb>` + aggregated `ticket.event`). **Tenant-wide `ticket_assigned` Toast removed** — assignee already gets bell flyout. |
| `voip-softphone.js`         | 710  | SIP.js 0.21.2 (CDN) + `CallEventConsumer`. Dial pad, DTMF, mute/hold/transfer/hangup, incoming-call modal |
| `notes-panel.js`            | 238  | Quick notes CRUD (6 colors, pinning, localStorage)                                       |
| `theme.js`                  | 77   | light/dark/system theme switcher (default dark). Loaded **synchronously in `<head>`** to prevent FOUC. matchMedia listener. |
| `agent-availability.js`     | 227  | Status toggle + persistence. Inline styles use `var(--status-info-dot)` etc.             |
| `command-palette.js`        | 337  | Cmd+K/Ctrl+K modal: static pages + dynamic search (200ms debounce on tickets/contacts)   |
| `custom-select.js`          | 371  | `CRMSelect` portal-rendered dropdown (searchable when >8 options)                     |
| `rich-editor.js`            | 191  | TipTap wrapper. Page-specific (NOT in base.html). TipTap loaded via importmap from `esm.sh` (inline in `tickets/detail.html`). |
| `keyboard-shortcuts.js`     | 318  | Hotkeys: j/k navigate · Enter open · Esc deselect · a/s/x row actions · Ctrl+K palette · c new · ? help · g d/t/c/b go-to. Disabled inside inputs. Injects runtime `<style>` using `var(--crm-primary)` etc. |

### LiveBus event domains observed in code
`user`, `membership`, `profile`, `comment`, `contact`, `company`, `account`, `contact_group`, `activity`, `reminder`, `newsfeed`, `ticket` (client-side normalisation), `notification` (from notification WS bridge), `live` (system event `live.reconnected`), `livebus` (internal channel-state events).

### Frontend subscribers (where events drive UI)

| Page | Events | Handler |
|------|--------|---------|
| `dashboard.html` | `ticket.event`, `notification.received`, `newsfeed.*`, `live.reconnected` | Debounced 600ms refresh of stats + recent activity |
| `tickets/list.html` | `ticket.created`, `ticket.updated/.assigned/.closed/.deleted`, `ticket.show_pending` | Debounced reload (page 1 only) |
| `tickets/detail.html` | filtered `comment.*`, `ticket.updated/.assigned/.closed/.deleted`, `live.reconnected` | Refetch ticket/comments/activity; toast on deletion |
| `contacts/list.html` | 12 `contact.*`/`company.*`/`account.*`/`contact_group.*` events, `live.reconnected` | Debounced 500ms list reload |
| `reminders/list.html` | 5 `reminder.*` verbs, `live.reconnected` | Debounced 500ms refetch reminders + stats |
| `app.js (global)` | `user.updated`, `livebus.channel_state` | Sidebar live updates; live-status pill |

All subscribers use a `document.visibilityState !== "hidden"` guard + `visibilitychange` listener so hidden tabs don't burn requests but catch up on focus.

## Static CSS (`static/css/`)

| File              | Lines  | Status                                                                       |
|-------------------|-------:|------------------------------------------------------------------------------|
| `custom-v15.css`  | 23,759 | **Live** — referenced by `base.html`. Crimson Black v9 (post-241e407 audit-log redesign). |
| `custom.css`      | 20,431 | Committed snapshot (no longer loaded; allowlisted in theme check).           |

Design system **"Crimson Black v9"** — fallback `#C1121F`/`#E11D2D` (NOT the `TenantSettings` default, which is `#6366F1`/`#F59E0B`). Foundation tokens in both `:root` and `[data-bs-theme="dark"]`: `--crm-text-on-{primary,accent,dark}`, `--crm-card-bg`, `--crm-input-bg{,-focus,-border}`, `--crm-scrollbar-*`, `--crm-skeleton-*`, `--crm-overlay`, plus the full `--crm-primary-*` (50–900) ramp and status ramp (`--status-info/success/warning/danger/neutral-*`).

### Theming architecture
- `apps/tenants/colors.py::derive_palette()` (159 lines) computes a ~21-value palette from `TenantSettings.primary_color/accent_color`. Picks `text_on_primary`/`text_on_accent` by WCAG 2.x luminance (white vs near-black `#0B0B0B`, whichever wins ≥ 4.5:1). Warns on contrast < AA.
- `base.html` emits ~35 CSS variables (palette + Bootstrap overrides + semantic-red retheme).
- **No hex literal in rule bodies.** Every brand red routes through `var(--crm-primary*)`. Every white-on-tenant-surface is `var(--crm-text-on-primary)`. `withAlpha(color, percent)` helper for var()-aware alpha (uses `color-mix(in srgb, … transparent)` when color is not a hex).
- **Dashboard chart colour map** (`STATUS_COLORS`, `PRIORITY_COLORS`) routes through `--status-*-dot` tokens, NOT `--crm-primary/--crm-accent`. Chart.js can't resolve `var()` directly — uses `cssVar(name, fallback)` + `resolveColor()` via `getComputedStyle`.
- **Regression guard:** `scripts/check_theme.py` (run via `make theme-check`) scans static/templates for new off-token hex literals. Current baseline tolerates 127 hex literals across 8 files (custom-v15.css 83, landing_crm 21, verify_email_sent 3, calendar 2, kanban 1, landing 15, reminders 1, tickets-list 1).

## External CDN dependencies (loaded by base.html)

| Asset                | Version | Source                                                   |
|----------------------|---------|----------------------------------------------------------|
| Google Fonts (Inter) | latest  | fonts.googleapis.com                                     |
| Bootstrap            | 5.3.3   | cdn.jsdelivr.net                                         |
| Tabler Icons         | 3.31.0  | cdn.jsdelivr.net                                         |
| Flatpickr            | latest  | jsdelivr (with cdnjs + unpkg fallbacks)                  |
| DOMPurify            | 3.2.4   | cdn.jsdelivr.net                                         |
| SIP.js               | 0.21.2  | cdn.jsdelivr.net (conditional — only when `voip_enabled`) |

TipTap is loaded via `esm.sh` importmap inline in `tickets/detail.html` and `knowledge/article.html` (NOT from base.html). Chart.js 4 and SortableJS 1.15.2 are page-specific CDN loads (dashboard, kanban).

## Tenant context processor (`apps/tenants/context_processors.py`, 81 lines)

Injects into every template:
1. `tenant`
2. `membership` (cached on `request._cached_tenant_membership`)
3. `user_role` — uses `membership.effective_role` (respects temporary-role overrides)
4. `is_admin` (hierarchy_level ≤ 10)
5. `is_admin_or_manager` (≤ 20)
6. `is_agent_or_above` (≤ 30)
7. `voip_enabled` — checks `VoIPSettings.is_active` for current tenant
8. **`tenant_palette`** — ~21-key dict from `derive_palette()`
9. `BASE_URL`

## Sidebar groups (`templates/includes/sidebar.html`, 157 lines)

Brand block, collapse button. **Several nav items are commented out** with `{% comment %}…{% endcomment %}` (Emails, Calls, Agents, Users, Settings, Billing) — the routes still exist but are reachable only via Settings page, the user-footer dropdown, or direct URL.

Footer dropdown (`.sidebar-user[data-current-user-id="{{user.id}}"]`) entries: Profile · Settings · **API Quickstart** · Sign Out.

Active groups (currently visible in sidebar):
- **Inbox:** Tickets · Messages
- **CRM:** Contacts · Boards · Knowledge Base
- **Planning:** Calendar · Reminders
- **Management** (gated): Analytics · Audit Log

Badge counters polled from `/api/v1/nav/badge-counts/` every 60s. Cap at 99 ("99+" rendered above).

## Notification flyout & bell animation

New notifications via WebSocket no longer fire a generic `Toast.info`. Instead `app.js`:
1. `ringBell()` — bell icon (`#notifDropdown`) gets `.is-ringing` for ~950ms (CSS swing + radial halo)
2. `showFlyout(data)` — bell-anchored card (`#notifFlyout`) slides in below the bell with **3-second** auto-fade and animated progress bar
3. `updateBadge(count, {bump:true})` — unread-count badge gains `.is-bumping` for a one-shot scale animation

All animations respect `@media (prefers-reduced-motion: reduce)`.
