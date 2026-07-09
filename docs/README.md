# Kanzen Documentation

Long-form reference docs for the Kanzen multi-tenant CRM/Ticketing/KB/VoIP platform.

> **Day-to-day source of truth:** `/CLAUDE.md` at repo root. The CLAUDE.md file is kept current with every refactor; the docs in this folder are deeper reference material.

## Layout

```
docs/
├── README.md                       (this file — index)
├── architecture.md                 (v1.0 design rationale, 2026-02 baseline — STALE)
├── ui-consistency-audit.md         (14-auditor UI-consistency sweep, 2026-06-23)
├── deploy/
│   └── protected-media.md          (prod media auth — X-Accel-Redirect)
├── reference/                       (verified 2026-05-11 — STALE; predate inbox_hub & 2026-06 features)
│   ├── codebase-inventory.md       (verified inventory: apps, models, routes, tasks)
│   ├── api-surface.md              (every REST endpoint + WebSocket consumer)
│   ├── frontend-surface.md         (templates, JS modules, CSS, frontend URLs)
│   └── infra-surface.md            (settings, Celery, PM2, env, requirements)
├── qa-audit-2026-06-14/            (end-to-end QA/security/perf audit, 38/100 pre-fix — drove Sprint 0; 11 files)
└── testing/
    ├── manual-testing-checklist.md (evergreen first-time-user playbook, Phases 0–9)
    └── qa-run-report-2026-06-29.md (headless HTTP/API QA run: 38 pass / 6 defects)
```

## When to use which

| Question                                | Look here                              |
|-----------------------------------------|----------------------------------------|
| "How is X implemented today?"           | `/CLAUDE.md` (project root)            |
| "Why was the system designed this way?" | `architecture.md`                      |
| "What does this app/model/route do?"    | `reference/codebase-inventory.md`      |
| "What endpoints exist?"                 | `reference/api-surface.md`             |
| "What pages/JS/CSS exist?"              | `reference/frontend-surface.md`        |
| "How is this deployed and configured?"  | `reference/infra-surface.md`           |
| "How do I manually test the app?"       | `testing/manual-testing-checklist.md`  |
| "What's broken / known defects?"        | `testing/qa-run-report-2026-06-29.md`  |

## Freshness markers

- `architecture.md` — Version 1.0, dated 2026-02-06. Pre-dates several 2026-04/05 features (auto-assign, IMAPPollState, Reminder M2M, temporary-role overrides, expanded ActivityLog/TicketActivity enums, kanzan-smtp PM2 process, fetch-inbound-emails Beat task). Treat as design rationale, not current shape.
- `reference/*.md` — Verified against `main @ bb36325` on **2026-05-11**. Should be re-verified after migrations / new ViewSets / schema changes.
- `/CLAUDE.md` — Should always reflect current state. If stale, rerun a deep-dive audit.
