# Kanzen — UI/UX & Accessibility Audit
**Date:** 2026-06-14 · Frontend = Bootstrap 5.3.3 + vanilla JS (no framework), TipTap editor, DOMPurify, design-system "Crimson Black v9.0"

> **Caveat:** This is a code-level UI audit (templates + JS). It was **not** validated in a live browser across viewports or with a real screen reader. Treat layout/responsive observations as code-derived; confirm with manual device testing before launch.

## Overall UX assessment
The design system is mature and consistent: tokenized colors/spacing/z-index/weight scales, a passing `theme-check` (no stray hex), dark-mode default with a synchronous FOUC guard, and thoughtful interactions (command palette, keyboard shortcuts, live status pill, reminder-due modal with reduced-motion guards). The **main weaknesses are accessibility** (keyboard/screen-reader gaps on custom controls) and a few **dead/mismatched UI affordances**.

## Accessibility findings (WCAG)

| ID | Sev | WCAG | Issue | Location | Fix |
|---|---|---|---|---|---|
| `templates-uiux-2` | **High** | 2.1.1 Keyboard, 4.1.2 Name/Role | Password show/hide toggle is an `<i>` with a click handler only — not focusable, no keyboard activation, no role/label | `templates/pages/login.html:42` | Use a `<button type="button" aria-label="Show password" aria-pressed>` |
| `templates-uiux-3` | Med | 4.1.2 | Reminder modal "Open" action is an `<a>` styled as a button while "Dismiss" is a `<button>` — semantic mismatch | `templates/base.html:166-168` | Use `<button>` or correct semantics |
| `templates-uiux-6` | Med | 4.1.2, 1.1.1 | Icon-only buttons (theme toggle, quick-create, notes) use `title` only, no `aria-label` | `templates/includes/navbar.html:34-36,100-102,137-139` | Add `aria-label` to each |
| `templates-uiux-8` | Med | 2.4.3 Focus Order | Create-ticket modal doesn't restore focus to the trigger on close | `templates/pages/emails/list.html:1083-1084,1124` | Restore focus on `hidden.bs.modal` |
| `templates-uiux-9` | Med | 4.1.2 | Reminder modal `aria-hidden` handling may suppress SR announcement of the alert | `templates/base.html:151-152` | Use `aria-live`/`role="alertdialog"`; manage `aria-hidden` via Bootstrap |

**Systemic a11y recommendations:**
- Audit all `KanzenSelect` portal-rendered selects (`static/js/custom-select.js`) and `command-palette.js`/`keyboard-shortcuts.js` runtime widgets for `role`, `aria-expanded`, `aria-activedescendant`, focus trapping, and ESC handling.
- Verify color contrast of the palette's `text_on_primary`/`text_on_accent` picks (the palette logs an AA warning when primary contrast < 4.5 — ensure tenants with poor brand colors still meet AA for body text, not just the primary swatch).
- Ensure all form inputs have associated `<label>`s and that error states are programmatically associated (`aria-describedby`).

## UI/UX correctness findings

| ID | Sev | Issue | Location | Fix |
|---|---|---|---|---|
| `frontend-js-1` | Med | Command-palette "New Contact" → dead `/contacts/new/` (real route `/contacts/create/`) | `static/js/command-palette.js:28` | Fix the URL |
| `feature-a-reminder-1` | Med | Reminder-due popup does **not** bump the sidebar Reminders badge (`NOTIF_TO_BADGE` maps only `reminder_overdue`) | `static/js/app.js:904-917` | Map `reminder_due` to the Reminders badge |
| `frontend-js-7` | Med | On pre-auth pages the ReminderAlerts modal degrades to a toast with no clear user feedback | `static/js/app.js:365-370` | Add explicit fallback messaging |
| `feature-b-overrides-8` | Low | Create-ticket description `<textarea>` has no `maxlength` (server caps at 20000, but no client hint) | `templates/pages/emails/list.html:414` | Add `maxlength="20000"` + counter |
| `inbox-hub-engine-10` | Low | "Hold" notification reuses `HUB_EMAIL_ASSIGNED` type with a `held` flag — semantic mismatch in the bell | `apps/inbox_hub/assignment.py:340` | Add a dedicated notification type/copy |
| `notifications-9` | **Review** | Notification email template renders `data.url` without validation/sanitization — link-injection / open-redirect risk in emails | `templates/notifications/email/notification.html:142-146`, `.txt:13-15` | Validate `url` is a relative path or allowlisted host before rendering |
| `knowledge-8` | Low | KB vote endpoint docs/body-params mismatch | `apps/knowledge/views.py:525-538` | Align OpenAPI schema with handler |

## Dead / orphaned UI
- `templates/includes/kb_sidebar_widget.html` — **orphan** (158 LOC), no includes; safe-delete candidate.
- `templates/pages/tickets/detail.html` — Delete-Ticket UI removed but ~44 lines of **dead macro JS** remain as no-ops.
- Inbox Hub cockpit never wires the live `claim/escalate/transition/note` endpoints — dead-but-present backend surface (UX confusion risk if partially exposed later).

## States (loading / empty / error)
- Reminder-due popup correctly queues + serializes modals and degrades to a sticky toast — good. WebSocket layers show a live status pill, but inconsistent reconnect caps (`frontend-js-2`) mean some channels die silently after ~10 attempts while the pill may still imply health — **fix the reconnect inconsistency and surface per-channel dead state.**
- Recommend a manual pass for empty-states and error-states on the large list pages (tickets, contacts, emails, inbox-hub) and for failed API calls in `api.js` (confirm user-visible error toasts, not silent console logs).

## Responsive / cross-browser (requires manual verification)
Not validated live. The app uses Bootstrap's grid + custom CSS (~25k LOC). Recommended manual matrix before launch: Chrome/Firefox/Safari × {desktop 1440, tablet 768, mobile 390}, focusing on the dense pages (`settings/tenant.html` 5.1k LOC, `kanban/board.html`, `tickets/detail.html` 3.7k LOC, `audit_log/list.html` 2.3k LOC) and the portaled popovers/selects.
