# UI Consistency Audit — Kanzen Suite

> Generated 2026-05-22 against branch `main` @ `26c989b`. Run `make theme-check` to confirm the hex-literal baseline is still at 125.

This is a **findings document**, not a prescription. The audit was scoped to "tokens + audit + targeted commits" (see conversation that produced it). It enumerates UI consistency gaps with `file:line` evidence so future passes can pick off batches without re-doing the survey.

## Executive summary

The token layer in [static/css/custom-v15.css](../static/css/custom-v15.css#L14-L400) is comprehensive for **colour**, **radius**, **shadow**, **transition** and **status semantics** — but is missing scales for **font-size**, **font-weight**, **spacing** and **z-index**. The result is hundreds of subtly-inconsistent literal values across the 23,759-line CSS, even though the colour/theming side is well-disciplined and guarded by `scripts/check_theme.py`.

The pattern is the same in every category: **the top 3-5 values account for the vast majority of uses**, so a token scale plus opportunistic refactors would clean up most of the noise without rewriting every component.

| Category                         | Distinct values | Top-3 share | Token coverage     |
| -------------------------------- | --------------- | ----------- | ------------------ |
| Colour (hex)                     | 125 baselined   | n/a         | Excellent (status + primary scales) |
| `border-radius`                  | 15+ literals    | 151/304 ≈ 50% | Partial — sm/lg/pill exist, gaps everywhere |
| `font-size`                      | 20+ literals    | 490/900+ ≈ 54% | **None** — every component picks raw rem |
| `font-weight`                    | 8+ literals     | 500/525 ≈ 95% | **None** — three weights do 95% of the work |
| `box-shadow`                     | 88 non-token shadows | n/a   | Partial — xs/sm/lg exist but rarely used |
| `z-index`                        | 22+ literals    | n/a         | **None** — 9 one-off values |

## 1. The token layer

### What's defined (excellent coverage)

[`:root`](../static/css/custom-v15.css#L14-L210) and [`[data-bs-theme="dark"]`](../static/css/custom-v15.css#L220-L395) expose:

- **Primary colour scale 50-900** + hover/active/dark/light/subtle/ring + focus-ring + glow.
- **Status ramp** (`--status-{success,info,warning,danger,neutral}-{text,bg,border,dot}`) — 20 tokens.
- **Foreground-on tokens** — `--crm-text-on-{primary,accent,dark,status}` (computed per-tenant by WCAG luminance for primary/accent; static `#FFFFFF` for status).
- **Surfaces** — `--crm-bg`, `--crm-surface`, `--crm-surface-elevated`, `--crm-bg-elevated-{1,2}`, `--crm-hover-bg`.
- **Borders** — `--crm-border`, `--crm-border-subtle`, `--crm-border-light`.
- **Text** — `--crm-text`, `--crm-text-primary`, `--crm-text-secondary`, `--crm-text-muted`.
- **Sidebar tokens** — 8 dedicated.
- **Radius** — `--crm-radius-sm` (8px), `--crm-radius` (10px), `--crm-radius-lg` (14px), `--crm-radius-pill` (9999px).
- **Shadow** — `--crm-shadow-{xs,sm,,-lg,-card-hover}`.
- **Transition** — `--crm-duration-{instant,fast,base,moderate,slow}` + 5 ease curves.
- **Component-scoped** — `--crm-chat-*`, `--crm-kanban-*`, `--crm-scrollbar-*`, `--crm-skeleton-*`, `--crm-overlay`.

### What's missing (gaps to add)

| Scale | Why it matters | Recommended tokens |
| ----- | -------------- | ------------------ |
| **Font-size** | 922+ raw declarations across CSS; the top 5 sizes do 606 of them. 0.7rem / 0.75rem / 0.78rem all coexist (within 0.08rem of each other). | `--crm-text-xs` (0.6875rem), `--crm-text-sm` (0.75rem), `--crm-text-base` (0.8125rem), `--crm-text-md` (0.875rem), `--crm-text-lg` (1rem), `--crm-text-xl` (1.125rem), `--crm-text-2xl` (1.25rem), `--crm-text-3xl` (1.5rem) |
| **Font-weight** | 525 declarations; 500/600/700 do 95%. Stray 450, 550, 650 values exist (e.g. `font-weight: 650` in 5 places — a deliberate "semibold-plus" that should either become a token or revert to 600). | `--crm-weight-normal` (400), `--crm-weight-medium` (500), `--crm-weight-semibold` (600), `--crm-weight-bold` (700) |
| **Radius** | Existing tokens (sm/lg/pill) cover only 159 of 304 raw values. 47× `6px` and 9× `7px`, 9× `3px` have no token. | Add `--crm-radius-xs` (4px), `--crm-radius-md` (6px). Migrate 7px→6px and 9px→8px in passes. |
| **Spacing** | No `--crm-space-*` exists; layouts pick `0.5rem` / `0.75rem` / `1rem` / `1.5rem` ad-hoc. Bootstrap's `--bs-spacer` lurks under it but isn't consistently used. | `--crm-space-1` (0.25rem) through `--crm-space-8` (3rem) on a 4px grid |
| **Z-index** | 22+ distinct values across CSS + templates + JS. 1070, 1081, 1100, 2000 are unique one-offs that suggest stacking-bug whack-a-mole. | `--crm-z-base`, `--crm-z-sticky` (10), `--crm-z-dropdown` (1000), `--crm-z-modal-backdrop` (1040), `--crm-z-modal` (1050), `--crm-z-popover` (1070), `--crm-z-tooltip` (1080), `--crm-z-flyout` (1090) |

### Existing token bugs

- **[static/css/custom-v15.css:6190](../static/css/custom-v15.css#L6190)** — Typo: `--crm-input-bg-border: #E4E4E7;` declared inside `.auth-form-panel` (the "light island"). Never referenced; should be `--crm-input-border`. Result: when the page is in dark mode, the auth-form input borders silently inherit `var(--crm-border)` = `#1F1F1F` from the dark token block instead of being pinned to the light value. Hard to notice on busy backgrounds — easy fix.

## 2. Hex-literal leakage (NEW, not in baseline)

The theme baseline ([scripts/.theme_baseline.json](../scripts/.theme_baseline.json)) tolerates 125 hex literals across 8 files. The `make theme-check` regression guard masks `<script>` blocks in HTML, so hex inside inline JavaScript is invisible to the check — but it's still hardcoded colour that should route through tokens. Below are findings that the guard misses:

- **[templates/pages/tickets/list.html:132](../templates/pages/tickets/list.html#L132)** — `<span class="tl-tab-dot" style="background:#A855F7;">` (purple "In Progress" tab dot). Should be `var(--status-info-dot)` or a new `--crm-tab-dot-in-progress` token. Note: theme-check already tolerates 1 hex in this file — likely this one — but the *fix* is the same.
- **[templates/pages/inbound_email/list.html:472](../templates/pages/inbound_email/list.html#L472)** — `dirBadge.style.border = '1px solid #C1121F33';` in JS. Should be `'1px solid var(--crm-primary-ring)'`.
- **[templates/pages/inbound_email/list.html:477](../templates/pages/inbound_email/list.html#L477)** — `dirBadge.style.border = '1px solid #A8A8AC33';` in JS. Should be `'1px solid var(--status-neutral-border)'`.
- **[templates/pages/contacts/list.html:301](../templates/pages/contacts/list.html#L301)** — `<div class="avatar avatar-sm" style="...color:#fff;">` in dynamically-rendered avatar row. Should be `color:var(--crm-text-on-primary)` so light tenants get readable contrast.

Pattern: **JS-injected inline styles bypass the theme guard**. Worth a follow-up enhancement to `scripts/check_theme.py` to peek into `<script>` blocks for hex inside string literals.

## 3. Inline styles that should be classes

The hex finds above are also examples of this category. Other notable inline-style hotspots:

- **[templates/pages/tickets/list.html:74-77](../templates/pages/tickets/list.html#L74-L77)** — `style="display:flex;gap:6px;align-items:center"` on `#customDateRange`. Trivially `class="d-flex align-items-center gap-1"` (Bootstrap utilities already loaded).
- **[templates/pages/tickets/detail.html:442](../templates/pages/tickets/detail.html#L442)** — Macro dropdown with compound `min-width:280px;max-height:320px;overflow-y:auto;position:absolute;z-index:1050` — five inline properties. Candidate for a single `.macro-dropdown` class.
- **[templates/pages/calendar.html:419,453](../templates/pages/calendar.html#L419)** — Modal dialogs override Bootstrap's responsive widths with hardcoded `max-width:420px` / `max-width:520px`. Use `modal-fullscreen-sm-down` + a dedicated `--bs-modal-width` instead.
- **[templates/pages/analytics/overview.html:32,44,56,68](../templates/pages/analytics/overview.html#L32)** — Four skeleton placeholders with inline `width:40px`. A shared `.skeleton-w-40` utility would dedupe.

## 4. Border-radius distribution (the headline gap)

```
 61× border-radius: 8px        → maps to --crm-radius-sm
 47× border-radius: 6px        → no token (add --crm-radius-md)
 43× border-radius: 10px       → maps to --crm-radius
 32× border-radius: 4px        → no token (add --crm-radius-xs)
 27× border-radius: 0.5rem     → same as 8px → --crm-radius-sm (rem/px mixed)
 16× border-radius: 999px      → near-equivalent of --crm-radius-pill (9999px)
 14× border-radius: 12px       → no token (could be --crm-radius-md or new step)
  9× border-radius: 3px        → suspiciously close to 4px; standardise
  9× border-radius: 100px      → another pill near-equivalent
  9× border-radius: 0.375rem   → same as 6px
  8× border-radius: 7px        → off-grid; should be 8px
  8× border-radius: 14px       → matches --crm-radius-lg
  7× border-radius: 9px        → off-grid; should be 8px or 10px
  7× border-radius: 20px       → no token (could be --crm-radius-xl)
  7× border-radius: 16px       → no token (could be --crm-radius-xl)
```

**Recommendation:** Add `--crm-radius-xs` (4px) and `--crm-radius-md` (6px); make `--crm-radius-pill` accept both 999 and 9999 by aliasing — or do a single sweep to normalise. Total potential conversions: ~250+ rules.

## 5. Font-size distribution

```
209× font-size: 0.8125rem      → would become --crm-text-base
150× font-size: 0.75rem        → --crm-text-sm
131× font-size: 0.6875rem      → --crm-text-xs
 72× font-size: 0.875rem       → --crm-text-md
 44× font-size: 1rem           → --crm-text-lg
 39× font-size: 0.625rem       → just below --crm-text-xs
 37× font-size: 0.9375rem      → between md and lg — suspect
 35× font-size: 1.125rem       → --crm-text-xl
 23× font-size: 0.7rem         → vs the 150× 0.75rem and 12× 0.78rem
 16× font-size: 0.85rem        → vs the 72× 0.875rem and 8× 0.8rem
```

The 23× 0.7rem, 16× 0.85rem, 12× 0.78rem and 8× 0.8rem rows are the smoking gun — these are accidental drift between components that "should" be the same size. Tokens won't fix them automatically, but the scale gives reviewers a clean snap-to grid.

## 6. Font-weight distribution

```
220× font-weight: 600          → --crm-weight-semibold
155× font-weight: 500          → --crm-weight-medium
125× font-weight: 700          → --crm-weight-bold
 10× font-weight: 800          → outside scale (extra bold)
  6× font-weight: 400          → --crm-weight-normal
  5× font-weight: 650          → suspect: deliberate "semibold-plus"
  1× font-weight: 550          → suspect: drift
  1× font-weight: 450          → suspect: drift
```

The 7 stray 450/550/650 values are review candidates. Either standardise to 500/600 or commit to documenting 650 as a deliberate sub-token (`--crm-weight-semibold-plus`).

## 7. Z-index map

```
 11× z-index: 1
  7× z-index: 2
  6× z-index: 1050        Bootstrap modal — expected
  5× z-index: 1080        sticky headers / tooltips
  4× z-index: 100
  3× z-index: 1040        Bootstrap modal-backdrop — expected
  2× z-index: 9999        escape hatches
  2× z-index: 1090        kanban filter (documented in CLAUDE.md)
  1× z-index: 99999       escape-hatch++ — review
  1× z-index: 2000        one-off — review
  1× z-index: 1100        between popover (1070) and unknown — review
  1× z-index: 1081        one off from 1080 — drift; either snap or document
  1× z-index: 1070        Bootstrap popover — expected
```

The four one-offs (99999, 2000, 1100, 1081) are the highest-risk findings here — they suggest someone bumped a value to "win" against another stacking context without checking what was below. A token scale (`--crm-z-modal: 1050; --crm-z-popover: 1070; --crm-z-flyout: 1090; --crm-z-toast: 1100`) gives reviewers something to compare against.

## 8. Box-shadow literals (88 non-token instances)

Existing tokens — `--crm-shadow-xs`, `--crm-shadow-sm`, `--crm-shadow`, `--crm-shadow-lg`, `--crm-shadow-card-hover` — cover the common cases but are rarely used in component rules. Sample literals that should map to tokens:

- **[static/css/custom-v15.css:1269,1282](../static/css/custom-v15.css#L1269)** — `0 1px 3px rgba(0,0,0,0.08)` + `0 2px 6px rgba(0,0,0,0.1)` → `var(--crm-shadow-sm)`.
- **[static/css/custom-v15.css:1765](../static/css/custom-v15.css#L1765)** — `0 12px 32px rgba(0,0,0,0.12)` → `var(--crm-shadow-lg)`.
- **[static/css/custom-v15.css:4146](../static/css/custom-v15.css#L4146)** — `0 16px 48px rgba(0,0,0,0.08)` → candidate for new `--crm-shadow-xl` token.

## 9. Dead CSS (zero references in templates or JS)

Confirmed via recursive grep across `templates/` + `static/js/`. The following classes are defined in [static/css/custom-v15.css](../static/css/custom-v15.css) but never referenced anywhere:

- `.ae-stat-card` and variants (`--online`, `--offline`, `--total`, `--capacity`) — lines [11341](../static/css/custom-v15.css#L11341)+
- `.ae-stat-icon`, `.ae-stat-info`, `.ae-stat-label`, `.ae-stat-value`
- `.ae-email-row` — line 861 (also `[data-density="compact"] .ae-email-row`)
- `.ae-filter-pill`, `.ae-filter-pills` — lines [11582, 11589](../static/css/custom-v15.css#L11582)
- `.ae-inbox-toolbar`, `.ae-search-box`
- `.activity-item-skeleton` — line [6084](../static/css/custom-v15.css#L6084)
- `.activity-loading` — line [5654](../static/css/custom-v15.css#L5654)

**Recommendation:** Verify by `git log -S "ae-stat-card"` to confirm they used to exist in a template and weren't just speculative. If confirmed dead, removing them saves ~100-200 lines.

## 10. Responsive + dark-mode gaps

### Dark-mode coverage

Spot-checked 10 representative classes — coverage is excellent for `.btn-*`, `.badge-*`, `.card`, `.modal`, `.table`, form controls. The one gap found:

- **`.tl-tab-dot`** — Inline-styled via `style="background:#A855F7"`. No class-level rule, so no dark-mode adjustment possible. (See finding §2 above.)

### Mobile fallbacks

Body classes `is-mobile-html` / `is-mobile` / `is-mobile-sm` are set by a JS IIFE in [base.html](../templates/base.html). Most components use them well. Gaps:

- **[templates/pages/groups/list.html:71](../templates/pages/groups/list.html#L71)** — `max-width:280px` on `.input-group` search field; no mobile fallback. On a 320px portrait viewport this leaves ~16px of marginal space.
- **[templates/pages/calendar.html:419,453](../templates/pages/calendar.html#L419)** — Modal dialogs hardcode widths instead of using Bootstrap's `modal-fullscreen-sm-down`.

## Top 8 priorities to fix (highest impact × smallest blast radius)

1. **Fix [`--crm-input-bg-border` typo at custom-v15.css:6190](../static/css/custom-v15.css#L6190)** → rename to `--crm-input-border`. 1-line change, fixes silent dark-mode bleed inside the auth-form "light island". (Trivial.)
2. **Add missing token scales to [`:root` + dark blocks](../static/css/custom-v15.css#L14-L395)** — `--crm-text-{xs,sm,base,md,lg,xl,2xl,3xl}`, `--crm-weight-{normal,medium,semibold,bold}`, `--crm-radius-{xs,md}`, `--crm-z-{dropdown,modal,popover,tooltip,flyout,toast}`. Zero behavioural change; gives every future refactor a target.
3. **Reduce in-flight hex-in-JS finds**: tickets/list.html:132, inbound_email/list.html:472+477, contacts/list.html:301 → token-replace. 4 lines, removes the 4 hex literals invisible to `make theme-check`.
4. **Enhance [scripts/check_theme.py](../scripts/check_theme.py)** to peek into `<script>` blocks for hex inside string literals (currently fully masked). One-time enhancement protects against drift.
5. **Replace inline styles in [tickets/list.html:74-77](../templates/pages/tickets/list.html#L74-L77) and [analytics/overview.html:32,44,56,68](../templates/pages/analytics/overview.html#L32)** with Bootstrap utility classes. Both are pure-mechanical conversions.
6. **Delete confirmed-dead `.ae-stat-*` + `.activity-*-skeleton` class trees** after a final `git log -S` check. ~150 lines saved.
7. **Snap 7px → 8px and 9px → 8px or 10px** in [custom-v15.css](../static/css/custom-v15.css). 15 occurrences total, all visual no-ops within 1px.
8. **Document or eliminate 99999/2000/1100/1081 z-index one-offs** in custom-v15.css — either snap to a token or comment-explain why the stacking context needs that magic number.

## Out of scope

This audit deliberately does not propose:

- A wholesale conversion of every `font-size: 0.8125rem;` to `var(--crm-text-base);`. That's a multi-day mechanical refactor; landing the tokens first lets it happen in batches per-component.
- Visual redesign of any page. The "professional dashboard appearance" is the existing Crimson Black v9 system; the audit only enumerates places where the *system isn't being followed*.
- Changes to the landing pages, email templates, or `settings/tenant.html` — these are explicitly allowlisted in the theme baseline because they need raw hex (gradient artwork, hex-color picker preview, etc.).
- New components or features. The user already has in-flight CSS work; piling more on top risks merge conflicts and review fatigue.

## Recommendations for future maintainability

- **Run [`make theme-check`](../Makefile) on pre-commit.** It's currently part of `make check` but not on a git hook. A pre-commit Husky-style hook would catch drift before it lands.
- **Add the missing tokens (item 2 above) *before* the next visual redesign**, so the redesign can reference them directly instead of inventing more raw values.
- **When adding a new page**, search [custom-v15.css](../static/css/custom-v15.css) for the closest-named existing component before writing new CSS — most new "card", "modal", "button" needs are already covered.
- **Treat z-index as a token**. A page that needs to bump z-index above an existing component should add a token, not pick `99999`. The current pile of magic numbers is a recipe for stacking-context bugs.
- **Treat the 5 stray font-weight values (450/550/650) and 7 stray border-radius values (3/7/9px) as bugs**, not features. Roll them into the nearest standard value on next touch.
