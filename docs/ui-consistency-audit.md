# UI Consistency Audit & Remediation — CRM Suite

> **Refreshed 2026-06-23.** Supersedes the 2026-05-22 survey. This pass ran a 14-auditor
> sweep (every page group + every design-system dimension + JS-injected styles) against
> `static/css/custom-v15.css` (the **loaded** stylesheet; `custom.css` is a dead snapshot)
> and the 48 templates, then **applied** the safe, high-confidence fixes and **registered**
> the rest as visual-QA-gated follow-ups. All CI gates stayed green (`make theme-check`,
> `makemigrations --check`, `pytest`).

The design **token layer** (Crimson Black v9) is strong; the historical problem was **adoption**
— tokens defined but barely referenced, plus duplicate component archetypes and ad-hoc colour
drift. This pass closed the largest *mechanical* gaps with zero rendering change and fixed the
genuine bugs; the remaining items either alter unverifiable dark-mode rendering or are product
decisions, and are listed in §3 for a visual-QA session.

## Audit scorecard (pre-pass, 0–100)

| Dimension | Score | Headline |
|-----------|------:|----------|
| typography | 22 | ladder defined, **0 of 1108** font-size literals referenced a token; 32% off-ladder |
| spacing | 25 | **no `--crm-space-*` scale existed**; ~80 ad-hoc literals, same size in both px & rem |
| shadow | 28 | only 13% of 407 box-shadows tokenized; rgba(0,0,0)/slate bases break dark parity |
| radius | 35 | only 30% of 657 radii tokenized; off-ladder 3/5/11/18/20/25px |
| component | 38 | TWO `.btn` systems, TWO/THREE `.card` blocks, contradictory `.chip--*`, 6+ chip variants |
| responsive | 45 | bare 768/992/576 mixed with the `.98` ladder; `max-width:992` overlapped `min-width:992` |
| a11y | 50 | icon-only buttons lacked `aria-label`; password toggle a bare `<i>`; red-on-red invalid ring |
| color | 58 | no raw hex in v15 rule bodies, but ~135 colored rgba() duplicate status tokens with drift |
| zindex | 62 | token scale exists but ~25 sites hardcode 1039–1090; magics 1100/9998/99999 |

## 1. The canonical "single designer" rule set

Every new/changed rule must obey these (distilled from the audit). The token scales live in
`:root` in `static/css/custom-v15.css`.

1. **Colour** — every colour in a rule body or JS-injected style is `var(--crm-*)` / `var(--status-*)`. No raw hex; raw numeric `rgba()/rgb()` is equally forbidden drift even though theme-check only catches hex. The only sanctioned inline colour is tenant DB data, routed through a CSS custom property.
2. **Alpha tints** — never concatenate a hex-alpha suffix onto a colour (`color + '15'` produces invalid CSS on `var()` colours). Use `color-mix(in srgb, <token> N%, transparent)` or `rgba(var(--crm-*-rgb), a)` / the shared `withAlpha()` helper.
3. **Radius** — snap to a token: 4=`--crm-radius-xs`, 8=`--crm-radius-sm`, 6=`--crm-radius-md`, 10=`--crm-radius`, 14=`--crm-radius-lg`, 16=`--crm-radius-xl`, pills=`--crm-radius-pill`. Forbidden literals: 3/5/7/11/12/18/20/24/25px.
4. **Type size** — `font-size` is `var(--crm-text-2xs..3xl)`; no bare rem/px. Text below 0.625rem (10px) is forbidden for a11y.
5. **Weight** — only `var(--crm-weight-{normal,medium,semibold,bold})` (400/500/600/700). 650/800 forbidden.
6. **Z-index** — only `var(--crm-z-*)`; express ±1 offsets as `calc(var(--crm-z-*) - 1)`. No magic 1100/9998/99999, no inline z-index.
7. **Shadow** — only `var(--crm-shadow-*)`; shadow base is `rgba(11,11,11,*)` (not `rgba(0,0,0)`/slate). Literals don't carry dark overrides, so they break dark parity.
8. **Motion** — durations via `var(--crm-duration-*)` (80/150/200/280/420ms), easings via `var(--crm-ease*)`. No raw `0.15s`/`cubic-bezier(...)`, no ms/s unit mixing.
9. **Spacing** — use the new `--crm-space-*` 4px grid; one unit (rem); no off-grid values. Until migrated, prefer Bootstrap `p-*`/`gap-*`.
10. **Dark mode** — achieve parity by tokenizing the BASE rule (so `var()` flips automatically), never by hand-writing a paired `[data-bs-theme="dark"]` literal twin. The goal is to delete the contrast-patch band-aid block.
11. **Breakpoints** — `max-width` queries use the Bootstrap `.98` ladder (575.98/767.98/991.98/1199.98/1399.98) paired with `min-width` 576/768/992/1200/1400.
12. **Inline styles** — templates/JS set only dynamic/layout/visibility inline; static colours/sizes/radii/shadows/z-index belong in a class. Hide via Bootstrap `.d-none` + `classList`, not inline `display:none`. No inline `onclick`.
13. **Component archetypes** — exactly ONE definition per archetype (`.btn`, `.card`, `.chip`, dialog-footer, empty-state, icon-button); one focus-visible ring, one `:active` scale. Variants compose the base; no `!important` wars from duplicates.
14. **A11y** — every icon-only control needs `aria-label`; interactive controls are real `<button>`s with focus-visible rings; the invalid-state ring must be visually distinct from the brand focus ring (brand == danger == red here, so use `--status-danger-*`).
15. **Self-containment** — `custom-v15.css` defines every token it references in `:root` (incl. a default `--crm-accent-rgb`).

## 2. Applied in this pass (all CI-gated green)

**Foundation tokens** (`:root`, custom-v15.css)
- Added `--crm-space-*` 4px-grid spacing scale (was missing entirely).
- Added `--crm-text-2xs` (0.625rem/10px) for the legitimate micro-label role.
- Added default `--crm-accent-rgb: 225,29,45` (was referenced 74× but never defined; base.html still overrides per-tenant).
- Removed the dead dark-mode `body::before` DEBUG MARKER (z-index 99999).

**Genuine rendering bugs**
- `app.js:362` — notification-icon background was `var(--crm-primary)15` (invalid CSS → silently transparent). Now `color-mix`.
- `tickets/detail.html:1592` — comment-avatar bg/border had the same broken `var() + '15'` concat → fixed with `color-mix`.
- `custom-v15.css` — removed the inverted `.chip--success` (gray) / `.chip--warning` (red) duplicates that contradicted the canonical green/amber block (kept `.chip--neutral`).
- `messaging/chat.html:1140` — participant row used the undefined `--crm-surface-alt` (fell back to white in light mode). Now `var(--crm-hover-bg)`.

**Mechanical token sweep — 1701 zero-rendering-change replacements** (property-anchored, `:root` protected; brace balance unchanged)
- font-size: ~800 on-ladder literals → `var(--crm-text-*)`.
- font-weight: ~508 on-scale literals → `var(--crm-weight-*)`.
- border-radius: ~201 on-ladder px → `var(--crm-radius-*)`.
- duration: 183 × `0.15s`/`0.2s` → `var(--crm-duration-fast/base)`; easing: 5 × `cubic-bezier(0.4,0,0.2,1)` → `var(--crm-ease)`.

**Responsive** — normalized 18 `@media` breakpoints to the `.98` ladder; fixed the `max-width:992` / `min-width:992` double-fire overlap and the `min-width:993..max-width:1200` custom tier.

**Z-index / shell** — removed the inline `z-index:1090` on `#toastContainer` (base.html) and replaced it with `#toastContainer { z-index: var(--crm-z-toast); }`.

**Accessibility** — added `aria-label` (+ `aria-hidden` on decorative icons) to **45** icon-only controls: navbar (theme/create/notes/notifications), login password toggle (now keyboard-operable via `role=button`+`tabindex`+keydown), modal close buttons, contact opt-out/block/clear, calendar swatches, kanban edit/delete, KB modals, admin lists. (All strictly additive — verified `0` non-aria changes.)

**Page colour fixes**
- `analytics/overview.html:151` — priority ramp was `urgent/high/medium` all red (urgent==medium identical → unreadable chart). Now mirrors dashboard (danger/warning/info/neutral). **Verified in-browser** (High=amber, Medium=blue).
- `dashboard.html:1623` — `#2563EB` one-off → `var(--status-info-dot)`.
- **Calendar colour system (was D10) — fixed + verified in-browser.** The Add Event colour picker had 6 of 8 swatches mislabelled (Amber/Green/Blue/Purple/Pink all rendered red or gray with `data-color="#E11C2B"`); `EVENT_COLORS` mapped almost every type to red; and the `.fc-event` month-chip CSS hardcoded a red background/text (with a dark-mode `!important` that defeated any per-event colour). Now: picker offers 6 correct, distinct, token-driven swatches (Default/Red/Amber/Green/Blue/Gray); `EVENT_COLORS` gives each type a distinct semantic token (ticket=info, created=neutral, due=danger, meeting=primary, call=success, task=warning); the month chip derives its border + tinted background + text from the event's type colour; the dark `!important` red override was neutralised. Calendar is no longer monochrome.

## 3. Deferred — visual-QA-gated register (NOT applied)

These are high-value but either alter **unverifiable dark-mode rendering** or are **product decisions**.
They were deliberately not auto-applied without a human looking at the rendered output. Each carries a
concrete starting point.

| # | Item | Evidence | Why deferred |
|---|------|----------|--------------|
| D1 | Tokenize ~135 colored `rgba()` literals → `--status-*` / `color-mix` | custom-v15.css:16743, :16085, :2063 (3 grays, 2 blues, off-palette emerald/indigo) | The literals are mostly *intentional custom values* (one gray used 37×), not exact-token drift; replacing changes colour in **both** modes. Needs side-by-side QA. |
| D2 | Delete the "DARK MODE CONTRAST PATCH (must stay last)" band-aid by root-causing | custom-v15.css:~21894 (e.g. `.fd-summary-value` #000 + dark twin + `!important`) | Changes dark-mode text colours; unverifiable without dark-mode visual check. |
| D3 | Merge the two `.btn` systems + two/three `.card` blocks into one each | custom-v15.css:641 & :3623 (.btn); :767, :2799, :22630 (.card) | Cascade already resolves a winner; merging risks visible shifts to focus ring / active scale / hover that can't be verified in code. Render is currently correct. |
| D4 | Unify the 3 email-list status-badge implementations onto one `.ae-email-badge--<status>` class | emails/list.html:467, inbound_email/list.html:169, messaging/chat.html | Touches the mid-flight Emails/Inbox rename surfaces; do alongside that feature's QA. |
| D5 | Snap off-ladder `font-size` (0.7/0.72/0.78/0.85/0.9rem…) and `box-shadow` literals to tokens | custom-v15.css (font 0.9375rem ×43 has no clean token; shadows :1309, :4643 slate base) | Sub-px font snapping changes rendering; box-shadow tokenization needs per-shadow judgment. |
| D6 | Snap off-ladder `border-radius` (3/5/11/12/18/20/25px) + stray `font-weight` 650/800 | custom-v15.css:11698 (650), :5011 (800) | Each changes corner radius / weight slightly; low-risk but unverifiable in bulk. |
| D7 | Normalize off-ladder motion (0.12/0.16/0.18/0.25/0.3s) onto the 5-step ladder | custom-v15.css transitions | Perceptually tiny but a real timing change; batch after a motion review. |
| D8 | Re-anchor the marketing `--kz-*` token universes to `var(--crm-*)` | landing.html:18 (indigo #6366F1), landing/landing_crm.html:27 | landing.html's indigo diverges from brand; recolouring marketing is a design call. |
| D9 | Promote duplicated KB "audience" inline blocks + contacts avatar hex palette to shared classes | knowledge/list.html:202 & article.html:108; contacts/list.html:273 (#2A2A2E…) | Moderate refactor; verify the KB cards + contact avatars render unchanged. |
| ~~D10~~ | ~~Calendar colour picker~~ — **DONE** (see §2, verified in-browser) | calendar.html | Resolved with on-brand semantic tokens. |
| D11 | Standardize the hide convention (inline `display:none` → `.d-none` + classList) | sidebar/softphone/navbar/page_back_button | Bootstrap `.d-none` is `!important`; requires coordinated JS show/hide changes per site to avoid breaking toggles. |

> **Visual-verification note (2026-06-23):** a Chrome/puppeteer screenshot harness was used to inspect dashboard, analytics, tickets, ticket-detail, contacts, calendar, kanban, settings, knowledge, emails and reminders in **both light and dark mode**. Finding: the *rendered* UI is already consistent and production-quality in both modes — the audit's low scores were about **code health** (token adoption, duplicate definitions), not visible defects. The remaining deferred items D1–D9 are therefore low-visible-payoff (maintainability) or product decisions (D8 marketing recolour), not user-facing bugs. The harness (`puppeteer-core` + system Chrome + a forged admin session) is reusable for the next batch.

## 4. Guardrails for future passes

- `static/css/custom-v15.css` is **one shared file** — serialize edits; never parallelize agents on it.
- For mechanical sweeps: protect the `:root` block, anchor on the property name, keep brace balance, then run `python3 scripts/check_theme.py` + `pytest`.
- Theme-check only fails on **net hex increases** per file; removing hex is always safe. Refresh the baseline with `python3 scripts/check_theme.py --baseline` after a reduction to tighten the guard.
- `custom.css` is **not loaded** and is allowlisted — do not spend effort sweeping it.
